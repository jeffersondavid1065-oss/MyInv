"""
Módulo para emitir facturas electrónicas a través de la API de Factus.
Cada negocio (usuario_id) tiene su propia cuenta de Factus, guardada en
Usuarios.factus_client_id / factus_client_secret / factus_username /
factus_password — no hay credenciales globales.

A diferencia de Alegra, en Factus POST /v2/bills/validate crea Y timbra la
factura ante la DIAN en un solo paso (no hay estado intermedio "abierta"), el
cliente y los ítems van embebidos en cada factura (no hay un recurso
"contacto"/"ítem" persistente que crear antes en la cuenta de Factus), y no
existe una API de pagos: el saldo pendiente / abonos de una venta a crédito
se maneja 100% dentro de MyInv (ver Creditos/Abonos), sin sincronizar nada
con Factus.
"""

import base64
import requests

BASE_URL_SANDBOX = "https://api-sandbox.factus.com.co"
BASE_URL_PRODUCCION = "https://api.factus.com.co"
# Mientras no haya negocios facturando en vivo, todo apunta al sandbox
# (gratis e ilimitado, espejo exacto de producción). Cambiar a
# BASE_URL_PRODUCCION cuando un negocio tenga credenciales reales de Factus.
BASE_URL = BASE_URL_SANDBOX

# --- Catálogos DIAN usados al armar el payload (ver Tablas de referencia de Factus) ---
TIPO_DOC_FACTUS = {"NIT": "31", "CC": "13"}
ORGANIZACION_FACTUS = {"NIT": "1", "CC": "2"}  # 1=Persona jurídica, 2=Persona natural
RESPONSABILIDADES_FACTUS = {
    "COMMON_REGIME": ["O-23"],      # Agente de retención de IVA / responsable de IVA
    "SIMPLIFIED_REGIME": ["R-99-PN"],  # No responsable
}
UNIDAD_MEDIDA_DEFECTO = "94"  # "unidad" — MyInv no distingue unidades finas ante la DIAN
CODIGO_IMPUESTO_IVA = "01"
PAYMENT_FORM_FACTUS = {"Efectivo": "1", "Transferencia": "1", "Mixto": "1", "Credito": "2"}
PAYMENT_METHOD_FACTUS = {"Efectivo": "10", "Transferencia": "47", "Mixto": "10"}


def _obtener_token(client_id, client_secret, username, password):
    """Pide un access_token nuevo (dura 1h en Factus). No se cachea entre
    reruns de Streamlit: el volumen de un negocio no justifica la complejidad
    de manejar refresh_token, y pedir uno nuevo por operación es gratis."""
    resp = requests.post(
        f"{BASE_URL}/oauth/token",
        headers={"Accept": "application/json"},
        data={
            "grant_type": "password",
            "client_id": client_id,
            "client_secret": client_secret,
            "username": username,
            "password": password,
        },
        timeout=15,
    )
    if resp.status_code != 200:
        return None, _mensaje_error(resp)
    return resp.json().get("access_token"), None


def _mensaje_error(resp):
    """Extrae el mensaje legible de una respuesta de error de Factus. Suele
    devolver {"message": "...", "data": {"errors": {...}}} — los errores de
    validación por campo van anidados dentro de "data", no al nivel raíz —
    pero a veces vienen al nivel raíz directamente, así que se revisan ambos.
    Si no se puede parsear, cae al texto crudo para no ocultar información."""
    try:
        data = resp.json()
        if isinstance(data, dict):
            partes = [str(data[k]) for k in ("message", "error", "error_description") if data.get(k)]
            errores = data.get("errors") or (data.get("data") or {}).get("errors")
            if errores:
                partes.append(str(errores))
            if partes:
                return " — ".join(partes)
    except ValueError:
        pass
    return resp.text


def probar_conexion(client_id, client_secret, username, password):
    """Verifica que unas credenciales de Factus funcionen, pidiendo un token."""
    try:
        token, error = _obtener_token(client_id, client_secret, username, password)
        if token:
            return True, "Conexión exitosa."
        return False, f"Credenciales rechazadas: {error}"
    except requests.RequestException as e:
        return False, f"Error de conexión: {e}"


def listar_rangos_numeracion(client_id, client_secret, username, password):
    """Diagnóstico: consulta los rangos de numeración (autorización DIAN) que
    tiene activos esta cuenta de Factus. Sin un rango activo, /v2/bills/validate
    rechaza cualquier factura con un 422 genérico sin detalle de campo."""
    try:
        token, error = _obtener_token(client_id, client_secret, username, password)
        if not token:
            return False, f"No se pudo autenticar con Factus: {error}"
        resp = requests.get(f"{BASE_URL}/v2/numbering-ranges", headers=_headers(token), timeout=15)
        if resp.status_code not in (200, 201):
            return False, f"Error consultando rangos ({resp.status_code}): {_mensaje_error(resp)}"
        return True, resp.json()
    except requests.RequestException as e:
        return False, f"Error de conexión: {e}"


def crear_rango_numeracion(client_id, client_secret, username, password, prefix, resolution_number, current):
    """Registra ante Factus el rango de numeración (resolución DIAN) de
    facturación electrónica de este negocio — paso único que debe hacerse
    una vez por cada cuenta de Factus nueva antes de poder facturar (sin
    esto, /v2/bills/validate rechaza todo con 'Version de API no disponible
    para esta empresa' o un 422 genérico). document='21' es el código fijo
    de Factus para rango de facturación electrónica (factura de venta)."""
    try:
        token, error = _obtener_token(client_id, client_secret, username, password)
        if not token:
            return False, f"No se pudo autenticar con Factus: {error}"
        payload = {
            "document": "21",
            "prefix": prefix,
            "resolution_number": resolution_number,
            "current": current,
        }
        resp = requests.post(f"{BASE_URL}/v2/numbering-ranges", headers=_headers(token), json=payload, timeout=15)
        if resp.status_code not in (200, 201):
            return False, f"El rango fue rechazado ({resp.status_code}): {_mensaje_error(resp)}"
        return True, resp.json().get("data", resp.json())
    except requests.RequestException as e:
        return False, f"Error de conexión: {e}"


def _resolver_numbering_range_id(token, document_nombre):
    """numbering_range_id es opcional solo si la cuenta tiene un único rango
    activo para ese tipo de documento; si hay más de uno, Factus lo exige y
    rechaza la factura/nota con un 422 sin detalle si no se envía. Se
    resuelve automáticamente contra /v2/numbering-ranges usando el rango
    activo más reciente. Devuelve None si no se puede determinar (la
    llamada seguirá intentando sin el campo, como antes)."""
    try:
        resp = requests.get(f"{BASE_URL}/v2/numbering-ranges", headers=_headers(token), timeout=15)
        if resp.status_code != 200:
            return None
        rangos = resp.json().get("data", {}).get("data", [])
        activos = [
            r for r in rangos
            if r.get("document") == document_nombre and r.get("is_active") and not r.get("is_expired")
        ]
        if not activos:
            return None
        activos.sort(key=lambda r: r.get("id", 0), reverse=True)
        return activos[0]["id"]
    except requests.RequestException:
        return None


def obtener_credenciales(uid):
    """Devuelve (client_id, client_secret, username, password) configurados
    por este negocio, o (None, None, None, None) si no ha configurado nada."""
    import queries

    negocio = queries.obtener_credenciales_factus(uid)
    if not negocio or not negocio.factus_client_id or not negocio.factus_client_secret:
        return None, None, None, None
    return negocio.factus_client_id, negocio.factus_client_secret, negocio.factus_username, negocio.factus_password


def _headers(token):
    return {"Authorization": f"Bearer {token}", "Accept": "application/json", "Content-Type": "application/json"}


def _construir_customer(cliente, municipio_code=None):
    """Arma el objeto 'customer' embebido en la factura/nota crédito a partir
    de un Cliente de MyInv.
    municipio_code: código DIVIPOLA. La documentación de Factus lo marca como
    opcional, pero algunas cuentas lo exigen igual — MyInv no rastrea ciudad
    por cliente, así que se usa el municipio configurado para el negocio como
    aproximación para todos sus clientes."""
    tipo_doc = cliente.tipo_documento or "CC"
    customer = {
        "identification_document_code": TIPO_DOC_FACTUS.get(tipo_doc, "13"),
        "identification": cliente.documento,
        "legal_organization_code": ORGANIZACION_FACTUS.get(tipo_doc, "2"),
        "tribute_code": "ZZ",
        "responsibilities": RESPONSABILIDADES_FACTUS.get(cliente.regimen, ["R-99-PN"]),
        "country_code": "CO",
    }
    if tipo_doc == "NIT":
        customer["company"] = cliente.nombre
        if cliente.digito_verificacion:
            customer["dv"] = str(cliente.digito_verificacion)
    else:
        customer["names"] = cliente.nombre
    if cliente.email:
        customer["email"] = cliente.email
    if municipio_code:
        customer["municipality_code"] = municipio_code
    return customer


def _construir_item(descripcion, cantidad, precio_unitario, descuento_linea, iva_porcentaje, codigo_ref):
    """Arma un renglón de la factura/nota crédito a partir de un renglón de
    Detalles_Venta de MyInv. precio_unitario llega con IVA incluido (como se
    guarda en MyInv) — se convierte al precio base sin IVA que espera Factus,
    igual que se hacía para Alegra."""
    iva_porcentaje = float(iva_porcentaje or 0)
    cantidad = float(cantidad or 1)
    precio_unitario = float(precio_unitario or 0)
    precio_base = precio_unitario / (1 + iva_porcentaje / 100) if iva_porcentaje > 0 else precio_unitario

    item = {
        "code_reference": codigo_ref,
        "name": str(descripcion)[:250],
        "quantity": f"{cantidad:.2f}",
        "discount_rate": "0.00",
        "price": f"{precio_base:.2f}",
        "unit_measure_code": UNIDAD_MEDIDA_DEFECTO,
        "standard_code": "999",
    }

    if cantidad and precio_unitario and descuento_linea:
        descuento_unitario = float(descuento_linea) / cantidad
        descuento_pct = (descuento_unitario / precio_unitario) * 100
        if descuento_pct > 0:
            item["discount_rate"] = f"{min(descuento_pct, 100):.2f}"

    if iva_porcentaje > 0:
        item["taxes"] = [{"code": CODIGO_IMPUESTO_IVA, "rate": f"{iva_porcentaje:.2f}"}]
    else:
        item["taxes"] = [{"code": CODIGO_IMPUESTO_IVA, "rate": "0.00", "is_excluded": True}]
    return item


def _construir_payment_details(tipo_pago, total, fecha_vencimiento=None):
    forma = PAYMENT_FORM_FACTUS.get(tipo_pago, "1")
    detalle = {
        "payment_form": forma,
        "payment_method_code": PAYMENT_METHOD_FACTUS.get(tipo_pago, "1") if forma == "1" else "1",
        "amount": f"{float(total):.2f}",
    }
    if forma == "2" and fecha_vencimiento:
        detalle["due_date"] = fecha_vencimiento.isoformat() if hasattr(fecha_vencimiento, "isoformat") else str(fecha_vencimiento)
    return [detalle]


def _pdf_base64_a_data_url(pdf_base64):
    """Factus ya entrega el PDF/XML codificados en base64 directo (no un
    enlace temporal como Alegra) — se guardan tal cual como enlace propio,
    sin necesidad de descargarlos de ningún lado."""
    if not pdf_base64:
        return None
    return f"data:application/pdf;base64,{pdf_base64}"


def _xml_base64_a_data_url(xml_base64):
    if not xml_base64:
        return None
    return f"data:application/xml;base64,{xml_base64}"


def _descargar_pdf(token, numero_factura):
    try:
        resp = requests.get(f"{BASE_URL}/v2/bills/{numero_factura}/download-pdf", headers=_headers(token), timeout=20)
        if resp.status_code == 200:
            return resp.json().get("data", {}).get("pdf_base_64_encoded")
    except requests.RequestException:
        pass
    return None


def _descargar_xml(token, numero_factura):
    try:
        resp = requests.get(f"{BASE_URL}/v2/bills/{numero_factura}/download-xml", headers=_headers(token), timeout=20)
        if resp.status_code == 200:
            return resp.json().get("data", {}).get("xml_base_64_encoded")
    except requests.RequestException:
        pass
    return None


def _descargar_pdf_nota_credito(token, numero_nc):
    try:
        resp = requests.get(f"{BASE_URL}/v2/credit-notes/{numero_nc}/download-pdf", headers=_headers(token), timeout=20)
        if resp.status_code == 200:
            return resp.json().get("data", {}).get("pdf_base_64_encoded")
    except requests.RequestException:
        pass
    return None


def _descargar_xml_nota_credito(token, numero_nc):
    try:
        resp = requests.get(f"{BASE_URL}/v2/credit-notes/{numero_nc}/download-xml", headers=_headers(token), timeout=20)
        if resp.status_code == 200:
            return resp.json().get("data", {}).get("xml_base_64_encoded")
    except requests.RequestException:
        pass
    return None


def facturar_venta(uid, venta_id):
    """
    Crea Y timbra ante la DIAN la factura electrónica de una venta ya
    registrada en MyInv, usando la cuenta de Factus propia del negocio 'uid'.
    A diferencia de Alegra, esto ocurre en un solo paso — no queda 'abierta'
    pendiente de emitir por separado.
    Devuelve (True, mensaje) o (False, mensaje).
    """
    import queries

    client_id, client_secret, username, password = obtener_credenciales(uid)
    if not client_id:
        return False, "Este negocio no tiene configurada su cuenta de facturación electrónica. Ve a Configuración → Facturación Electrónica."

    venta = queries.obtener_venta_para_facturar(uid, venta_id)
    if not venta:
        return False, "Venta no encontrada."
    if venta.factura_alegra_id:
        return False, "Esta venta ya tiene una factura creada."
    if not venta.cliente_id:
        return False, "Esta venta no tiene un cliente asociado. Selecciona un cliente registrado (con documento) para poder facturar electrónicamente."

    cliente = queries.obtener_datos_facturacion_cliente(uid, venta.cliente_id)
    if not cliente:
        return False, "Cliente no encontrado."

    # Red de seguridad: si el negocio está configurado como que no declara
    # IVA, nunca se le manda IVA a la factura aunque algún producto haya
    # quedado con un % viejo.
    iva_permitido = queries.tiene_iva_habilitado(uid)

    renglones = queries.obtener_items_venta(venta_id)
    if not renglones:
        return False, "Esta venta no tiene ítems para facturar."

    items_payload = [
        _construir_item(
            r.nombre_producto, r.cantidad, r.precio_unitario, r.descuento,
            (r.iva_porcentaje or 0) if iva_permitido else 0, f"VENTA-{venta_id}-{i}",
        )
        for i, r in enumerate(renglones, start=1)
    ]

    fecha_vencimiento = None
    if venta.tipo_pago == "Credito":
        credito = queries.obtener_credito_de_venta(venta_id)
        if credito and credito.fecha_limite:
            fecha_vencimiento = credito.fecha_limite

    municipio_code = queries.obtener_municipio_taller(uid)

    try:
        token, error = _obtener_token(client_id, client_secret, username, password)
        if not token:
            return False, f"No se pudo autenticar con Factus: {error}"

        payload = {
            "reference_code": f"MYINV-{uid}-{venta_id}",
            "document": "01",
            "payment_details": _construir_payment_details(venta.tipo_pago, venta.total, fecha_vencimiento),
            "customer": _construir_customer(cliente, municipio_code),
            "items": items_payload,
        }
        if getattr(venta, "notas", None):
            payload["observation"] = str(venta.notas)[:250]
        numbering_range_id = _resolver_numbering_range_id(token, "Factura de Venta")
        if numbering_range_id:
            payload["numbering_range_id"] = numbering_range_id

        resp = requests.post(f"{BASE_URL}/v2/bills/validate", headers=_headers(token), json=payload, timeout=30)
        if resp.status_code not in (200, 201):
            queries.guardar_resultado_factura(venta_id, estado="error")
            return False, f"La factura fue rechazada ({resp.status_code}): {_mensaje_error(resp)}"

        data = resp.json().get("data", {})
        bill = data.get("bill", data)
        numero_factura = bill.get("number") or bill.get("bill_number")
        cufe = bill.get("cufe") or (bill.get("legal_stamp") or {}).get("cufe")
        is_validated = bill.get("is_validated", True)

        if not is_validated:
            queries.guardar_resultado_factura(venta_id, estado="error")
            return False, (
                "La factura se creó pero la DIAN no la validó todavía. Revisa el estado en Factus "
                "e inténtalo de nuevo (usa 'Eliminar no validada' desde Factus si necesitas reintentar)."
            )

        pdf_url = _pdf_base64_a_data_url(_descargar_pdf(token, numero_factura)) if numero_factura else None
        xml_url = _xml_base64_a_data_url(_descargar_xml(token, numero_factura)) if numero_factura else None

        queries.guardar_resultado_factura(
            venta_id,
            reference_code=payload["reference_code"],
            cufe=cufe,
            pdf_url=pdf_url,
            xml_url=xml_url,
            estado="emitida",
            prefijo=None,
            numero=numero_factura,
        )

        return True, f"Factura {numero_factura} emitida ante la DIAN."
    except requests.RequestException as e:
        queries.guardar_resultado_factura(venta_id, estado="error")
        return False, f"Error de conexión al conectar con Factus para crear la factura: {e}"


def anular_factura_venta(uid, venta_id):
    """
    Emite la nota crédito en Factus que anula la factura electrónica de una
    venta (cuando esa venta se anula/devuelve en MyInv). No hace nada (y no
    es un error) si la venta nunca tuvo factura electrónica emitida.
    Devuelve (True, mensaje) o (False, mensaje).
    """
    import queries

    venta = queries.obtener_venta_para_facturar(uid, venta_id)
    if not venta:
        return False, "Venta no encontrada."
    if venta.factura_estado != "emitida" or not venta.factura_alegra_id:
        return True, "Esta venta no tenía factura electrónica emitida, no se requiere nota crédito."
    if venta.nota_credito_alegra_id:
        return False, "Esta venta ya tiene una nota crédito emitida."

    client_id, client_secret, username, password = obtener_credenciales(uid)
    if not client_id:
        return False, "Este negocio no tiene configurada su cuenta de facturación electrónica."

    cliente = queries.obtener_datos_facturacion_cliente(uid, venta.cliente_id)
    if not cliente:
        return False, "No se pudo obtener el cliente."

    iva_permitido = queries.tiene_iva_habilitado(uid)

    renglones = queries.obtener_items_venta(venta_id)
    if not renglones:
        return False, "No hay ítems para la nota crédito."

    items_payload = [
        _construir_item(
            r.nombre_producto, r.cantidad, r.precio_unitario, r.descuento,
            (r.iva_porcentaje or 0) if iva_permitido else 0, f"VENTA-{venta_id}-{i}",
        )
        for i, r in enumerate(renglones, start=1)
    ]

    municipio_code = queries.obtener_municipio_taller(uid)
    reference_code_nc = f"MYINV-NC-{uid}-{venta_id}"

    try:
        token, error = _obtener_token(client_id, client_secret, username, password)
        if not token:
            return False, f"No se pudo autenticar con Factus: {error}"

        payload = {
            "reference_code": reference_code_nc,
            "correction_concept_code": "2",  # Anulación de factura electrónica
            "bill_number": venta.factura_numero,
            "payment_details": _construir_payment_details(venta.tipo_pago or "Efectivo", venta.total),
            "customer": _construir_customer(cliente, municipio_code),
            "items": items_payload,
        }
        numbering_range_id = _resolver_numbering_range_id(token, "Nota Crédito")
        if numbering_range_id:
            payload["numbering_range_id"] = numbering_range_id

        resp = requests.post(f"{BASE_URL}/v2/credit-notes/validate", headers=_headers(token), json=payload, timeout=30)
        if resp.status_code not in (200, 201):
            return False, f"La nota crédito fue rechazada ({resp.status_code}): {_mensaje_error(resp)}"

        data = resp.json().get("data", {})
        nota = data.get("credit_note", data)
        numero_nc = nota.get("number")

        pdf_url_nc = _pdf_base64_a_data_url(_descargar_pdf_nota_credito(token, numero_nc)) if numero_nc else None
        xml_url_nc = _xml_base64_a_data_url(_descargar_xml_nota_credito(token, numero_nc)) if numero_nc else None

        queries.guardar_nota_credito(
            venta_id, reference_code_nc, pdf_url=pdf_url_nc, xml_url=xml_url_nc,
            prefijo=None, numero=numero_nc,
        )
        return True, f"Nota crédito emitida ({numero_nc})."
    except requests.RequestException as e:
        return False, f"Error de conexión al conectar con Factus para crear la nota crédito: {e}"


def refrescar_url_factura(uid, venta_id):
    """
    Devuelve (pdf_url, xml_url) de la factura de esta venta. A diferencia de
    Alegra, Factus no da enlaces temporales — lo que se guardó al facturar ES
    la copia permanente, así que esto solo lee lo ya guardado (sin llamar a
    la API de nuevo).
    """
    import queries

    venta = queries.obtener_venta_para_facturar(uid, venta_id)
    if not venta:
        return None, None
    return venta.factura_pdf_url, venta.factura_xml_url


def refrescar_url_nota_credito(uid, venta_id):
    """Igual que refrescar_url_factura(), pero para la nota crédito."""
    import queries

    venta = queries.obtener_venta_para_facturar(uid, venta_id)
    if not venta:
        return None, None
    return venta.nota_credito_pdf_url, venta.nota_credito_xml_url


def _es_copia_propia(url):
    """True si el enlace ya contiene el archivo adentro (data:...;base64,...).
    isinstance(url, str) primero: un valor vacío proveniente de un DataFrame
    de pandas llega como NaN (float), no None."""
    return isinstance(url, str) and url.startswith("data:")


def _bytes_desde_enlace_propio(url):
    if not _es_copia_propia(url):
        return None
    try:
        _, b64data = url.split(",", 1)
        return base64.b64decode(b64data)
    except Exception:
        return None


def mostrar_documento(contenedor, etiqueta, url, nombre_archivo, mime_type):
    """
    Muestra un botón para abrir/descargar un PDF o XML de una factura o nota
    crédito. Factus siempre entrega el documento como copia propia (data:),
    así que se usa st.download_button con los bytes decodificados: varios
    navegadores (Chrome incluido) bloquean abrir un enlace data: en una
    pestaña nueva.
    contenedor: st, o una columna devuelta por st.columns().
    """
    if not isinstance(url, str) or not url:
        return
    contenido = _bytes_desde_enlace_propio(url)
    if contenido:
        contenedor.download_button(
            etiqueta, data=contenido, file_name=nombre_archivo,
            mime=mime_type, use_container_width=True,
        )
    else:
        contenedor.link_button(etiqueta, url, use_container_width=True)
