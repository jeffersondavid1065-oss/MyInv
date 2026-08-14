"""
Módulo para emitir facturas electrónicas a través de la API de Factus.
Cada negocio (usuario_id) tiene su propia cuenta de Factus, guardada en
Usuarios.factus_client_id / factus_client_secret / factus_username /
factus_password — no hay credenciales globales.

Habla la API v1 de Factus (no v2): la cuenta de producción de Electricos.DR
(primer negocio facturando en vivo) solo tiene habilitado acceso a v1 — v2
responde 403 "Version de API no disponible para esta empresa" en cualquier
endpoint para esa cuenta. Si en el futuro un negocio nuevo solo tiene v2,
esto debe volverse configurable por negocio en vez de un único módulo v1.

Particularidades de v1 frente a v2 (y frente a Alegra):
- Los campos de cliente/producto van por ID numérico interno de Factus
  (identification_document_id, legal_organization_id, tribute_id,
  unit_measure_id, standard_code_id), no por código de texto — los IDs fijos
  que usa este módulo salen de las tablas de referencia de Factus v1.
- items.price va CON impuestos incluidos (igual que se guarda en MyInv), a
  diferencia de v2 que pedía el precio base sin IVA.
- Para anular una factura con nota crédito, v1 exige el bill_id interno de
  Factus (no el número de factura) — se resuelve consultando la factura por
  su número justo antes de crear la nota.
- POST /v1/bills/validate crea Y timbra la factura ante la DIAN en un solo
  paso (no hay estado intermedio "abierta"), el cliente y los ítems van
  embebidos en cada factura, y no existe una API de pagos: el saldo
  pendiente / abonos de una venta a crédito se maneja 100% dentro de MyInv
  (ver Creditos/Abonos), sin sincronizar nada con Factus.
"""

import base64
import requests

BASE_URL_SANDBOX = "https://api-sandbox.factus.com.co"
BASE_URL_PRODUCCION = "https://api.factus.com.co"
# Electricos.DR es el primer negocio facturando en vivo, con cuenta de
# producción real de Factus — todo MyInv apunta a producción desde aquí.
# Si en el futuro se necesita un negocio en modo pruebas, esto debe
# convertirse en un valor por negocio (columna en Usuarios) en vez de global.
BASE_URL = BASE_URL_PRODUCCION

# --- Catálogos de Factus v1 (ver Tablas de referencia de Factus v1) ---
TIPO_DOC_ID_FACTUS = {"NIT": 6, "CC": 3, "CE": 5, "PAS": 7, "TI": 2}  # IDs tipos de documentos de identidad
ORGANIZACION_ID_FACTUS = {"NIT": 1, "CC": 2}   # IDs tipos de organizaciones (1=Jurídica, 2=Natural)
TRIBUTE_ID_CLIENTE = 21                         # ZZ - No aplica (fijo, MyInv no rastrea régimen por ID Factus)
TRIBUTE_ID_IVA_ITEM = 1                         # IVA en items (tabla de tributos de productos)
UNIT_MEASURE_ID_DEFECTO = 70                    # "unidad" (code 94) — MyInv no distingue unidades finas
STANDARD_CODE_ID_DEFECTO = 1                    # Estándar de adopción del contribuyente
DOCUMENTO_FACTURA = "01"                        # Factura electrónica de venta
CORRECCION_ANULACION = 2                        # Código de corrección: anulación de factura electrónica
CUSTOMIZATION_NC_CON_FACTURA = 20               # Nota crédito que referencia una factura electrónica
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


def consultar_adquiriente_dian(client_id, client_secret, username, password, tipo_documento, numero_documento):
    """Consulta directamente a la DIAN (a través de Factus) el nombre/razón
    social y correo asociados a un documento, para autocompletar el registro
    de un cliente nuevo sin escribirlos a mano. Solo devuelve esos dos campos
    — dígito de verificación, régimen y dirección siguen siendo manuales."""
    doc_id = TIPO_DOC_ID_FACTUS.get(tipo_documento)
    if not doc_id or not str(numero_documento or "").strip():
        return False, "Tipo o número de documento inválido."
    try:
        token, error = _obtener_token(client_id, client_secret, username, password)
        if not token:
            return False, f"No se pudo autenticar con Factus: {error}"
        resp = requests.get(
            f"{BASE_URL}/v1/dian/acquirer",
            headers=_headers(token),
            params={"identification_document_id": doc_id, "identification_number": str(numero_documento).strip()},
            timeout=15,
        )
        if resp.status_code != 200:
            return False, f"La DIAN no encontró datos para ese documento ({resp.status_code}): {_mensaje_error(resp)}"
        data = resp.json().get("data", {})
        if not data.get("name"):
            return False, "La DIAN no devolvió datos para ese documento."
        return True, {"nombre": data.get("name"), "email": data.get("email")}
    except requests.RequestException as e:
        return False, f"Error de conexión: {e}"


def listar_rangos_numeracion(client_id, client_secret, username, password):
    """Diagnóstico: consulta los rangos de numeración (autorización DIAN) que
    tiene activos esta cuenta de Factus. Sin un rango activo, /v1/bills/validate
    rechaza cualquier factura con un 422 genérico sin detalle de campo."""
    try:
        token, error = _obtener_token(client_id, client_secret, username, password)
        if not token:
            return False, f"No se pudo autenticar con Factus: {error}"
        resp = requests.get(f"{BASE_URL}/v1/numbering-ranges", headers=_headers(token), timeout=15)
        if resp.status_code not in (200, 201):
            return False, f"Error consultando rangos ({resp.status_code}): {_mensaje_error(resp)}"
        return True, resp.json()
    except requests.RequestException as e:
        return False, f"Error de conexión: {e}"


def crear_rango_numeracion(client_id, client_secret, username, password, prefix, resolution_number, current):
    """Registra ante Factus el rango de numeración (resolución DIAN) de
    facturación electrónica de este negocio — paso único que debe hacerse
    una vez por cada cuenta de Factus nueva antes de poder facturar.
    document='21' es el código fijo de Factus para rango de facturación
    electrónica (factura de venta)."""
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
        resp = requests.post(f"{BASE_URL}/v1/numbering-ranges", headers=_headers(token), json=payload, timeout=15)
        if resp.status_code not in (200, 201):
            return False, f"El rango fue rechazado ({resp.status_code}): {_mensaje_error(resp)}"
        return True, resp.json().get("data", resp.json())
    except requests.RequestException as e:
        return False, f"Error de conexión: {e}"


def _resolver_numbering_range_id(token, document_nombre):
    """numbering_range_id es opcional solo si la cuenta tiene un único rango
    activo para ese tipo de documento; si hay más de uno, Factus lo exige y
    rechaza la factura/nota con un 422 sin detalle si no se envía. Se
    resuelve automáticamente contra /v1/numbering-ranges usando el rango
    activo más reciente. Devuelve None si no se puede determinar (la
    llamada seguirá intentando sin el campo, como antes)."""
    try:
        resp = requests.get(f"{BASE_URL}/v1/numbering-ranges", headers=_headers(token), timeout=15)
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


def _obtener_bill_id(token, numero_factura):
    """v1 exige el ID interno de la factura (no su número) para crear la nota
    crédito que la anula — se resuelve consultando la factura por su número
    justo antes de armar la nota."""
    try:
        resp = requests.get(f"{BASE_URL}/v1/bills/show/{numero_factura}", headers=_headers(token), timeout=15)
        if resp.status_code != 200:
            return None
        data = resp.json().get("data", {})
        bill = data.get("bill", data)
        return bill.get("id")
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


def _construir_customer(cliente):
    """Arma el objeto 'customer' embebido en la factura/nota crédito a partir
    de un Cliente de MyInv. No se envía municipality_id: en v1 ese campo pide
    el ID interno de Factus para el municipio (no el código DIVIPOLA que
    guarda MyInv), y la documentación lo marca como opcional."""
    tipo_doc = cliente.tipo_documento or "CC"
    customer = {
        "identification_document_id": TIPO_DOC_ID_FACTUS.get(tipo_doc, 3),
        "identification": cliente.documento,
        "legal_organization_id": ORGANIZACION_ID_FACTUS.get(tipo_doc, 2),
        "tribute_id": TRIBUTE_ID_CLIENTE,
    }
    if tipo_doc == "NIT":
        customer["company"] = cliente.nombre
        if cliente.digito_verificacion:
            customer["dv"] = str(cliente.digito_verificacion)
    else:
        customer["names"] = cliente.nombre
    if cliente.email:
        customer["email"] = cliente.email
    return customer


def _construir_item(descripcion, cantidad, precio_unitario, descuento_linea, iva_porcentaje, codigo_ref):
    """Arma un renglón de la factura/nota crédito a partir de un renglón de
    Detalles_Venta de MyInv. A diferencia de v2, v1 espera items.price CON
    impuestos incluidos — igual que se guarda en MyInv — así que no hay que
    quitarle el IVA. quantity debe ser entero en v1 (MyInv no vende
    fracciones de unidad en la práctica, así que se redondea)."""
    iva_porcentaje = float(iva_porcentaje or 0)
    cantidad = float(cantidad or 1)
    precio_unitario = float(precio_unitario or 0)

    item = {
        "code_reference": codigo_ref,
        "name": str(descripcion)[:250],
        "quantity": int(round(cantidad)),
        "discount_rate": 0.0,
        "price": round(precio_unitario, 2),
        "tax_rate": f"{iva_porcentaje:.2f}",
        "unit_measure_id": UNIT_MEASURE_ID_DEFECTO,
        "standard_code_id": STANDARD_CODE_ID_DEFECTO,
        "is_excluded": 0 if iva_porcentaje > 0 else 1,
        "tribute_id": TRIBUTE_ID_IVA_ITEM,
    }

    if cantidad and precio_unitario and descuento_linea:
        descuento_unitario = float(descuento_linea) / cantidad
        descuento_pct = (descuento_unitario / precio_unitario) * 100
        if descuento_pct > 0:
            item["discount_rate"] = round(min(descuento_pct, 100), 2)

    return item


def _campos_pago(tipo_pago, fecha_vencimiento=None):
    """v1 manda la forma/método de pago como campos sueltos del documento
    (no un array payment_details como v2)."""
    forma = PAYMENT_FORM_FACTUS.get(tipo_pago, "1")
    campos = {
        "payment_form": forma,
        "payment_method_code": PAYMENT_METHOD_FACTUS.get(tipo_pago, "10") if forma == "1" else "1",
    }
    if forma == "2" and fecha_vencimiento:
        campos["payment_due_date"] = fecha_vencimiento.isoformat() if hasattr(fecha_vencimiento, "isoformat") else str(fecha_vencimiento)
    return campos


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
        resp = requests.get(f"{BASE_URL}/v1/bills/download-pdf/{numero_factura}", headers=_headers(token), timeout=20)
        if resp.status_code == 200:
            return resp.json().get("data", {}).get("pdf_base_64_encoded")
    except requests.RequestException:
        pass
    return None


def _descargar_xml(token, numero_factura):
    try:
        resp = requests.get(f"{BASE_URL}/v1/bills/download-xml/{numero_factura}", headers=_headers(token), timeout=20)
        if resp.status_code == 200:
            return resp.json().get("data", {}).get("xml_base_64_encoded")
    except requests.RequestException:
        pass
    return None


def _descargar_pdf_nota_credito(token, numero_nc):
    try:
        resp = requests.get(f"{BASE_URL}/v1/credit-notes/download-pdf/{numero_nc}", headers=_headers(token), timeout=20)
        if resp.status_code == 200:
            return resp.json().get("data", {}).get("pdf_base_64_encoded")
    except requests.RequestException:
        pass
    return None


def _descargar_xml_nota_credito(token, numero_nc):
    try:
        resp = requests.get(f"{BASE_URL}/v1/credit-notes/download-xml/{numero_nc}", headers=_headers(token), timeout=20)
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

    try:
        token, error = _obtener_token(client_id, client_secret, username, password)
        if not token:
            return False, f"No se pudo autenticar con Factus: {error}"

        payload = {
            "reference_code": f"MYINV-{uid}-{venta_id}",
            "document": DOCUMENTO_FACTURA,
            "customer": _construir_customer(cliente),
            "items": items_payload,
        }
        payload.update(_campos_pago(venta.tipo_pago, fecha_vencimiento))
        if getattr(venta, "notas", None):
            payload["observation"] = str(venta.notas)[:250]
        numbering_range_id = _resolver_numbering_range_id(token, "Factura de Venta")
        if numbering_range_id:
            payload["numbering_range_id"] = numbering_range_id

        resp = requests.post(f"{BASE_URL}/v1/bills/validate", headers=_headers(token), json=payload, timeout=30)
        if resp.status_code not in (200, 201):
            queries.guardar_resultado_factura(venta_id, estado="error")
            return False, f"La factura fue rechazada ({resp.status_code}): {_mensaje_error(resp)}"

        data = resp.json().get("data", {})
        bill = data.get("bill", data)
        numero_factura = bill.get("number")
        cufe = bill.get("cufe")
        validada = bill.get("status") == 1

        if not validada:
            queries.guardar_resultado_factura(venta_id, estado="error")
            return False, (
                "La factura se creó pero la DIAN no la validó todavía. Revisa el estado en Factus "
                "e inténtalo de nuevo (usa 'Eliminar factura no validada' desde Factus si necesitas reintentar)."
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

    reference_code_nc = f"MYINV-NC-{uid}-{venta_id}"

    try:
        token, error = _obtener_token(client_id, client_secret, username, password)
        if not token:
            return False, f"No se pudo autenticar con Factus: {error}"

        bill_id = _obtener_bill_id(token, venta.factura_numero)
        if not bill_id:
            return False, "No se pudo obtener la factura original en Factus para generar la nota crédito."

        payload = {
            "reference_code": reference_code_nc,
            "correction_concept_code": CORRECCION_ANULACION,
            "customization_id": CUSTOMIZATION_NC_CON_FACTURA,
            "bill_id": bill_id,
            "payment_method_code": PAYMENT_METHOD_FACTUS.get(venta.tipo_pago or "Efectivo", "10"),
            "customer": _construir_customer(cliente),
            "items": items_payload,
        }
        numbering_range_id = _resolver_numbering_range_id(token, "Nota Crédito")
        if numbering_range_id:
            payload["numbering_range_id"] = numbering_range_id

        resp = requests.post(f"{BASE_URL}/v1/credit-notes/validate", headers=_headers(token), json=payload, timeout=30)
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
