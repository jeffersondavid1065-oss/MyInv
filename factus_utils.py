"""
Módulo para emitir facturas electrónicas a través de la API de Factus.
Cada negocio (usuario_id) tiene su propia cuenta de Factus, guardada en
Usuarios.factus_client_id / factus_client_secret / factus_username /
factus_password — no hay credenciales globales.

Habla la API v2 de Factus: Factus ya habilitó v2 para la cuenta de producción
de Electricos.DR (antes solo tenía v1 — ver historial de este archivo si v2
vuelve a devolver 403 "Version de API no disponible para esta empresa"). Si
en el futuro un negocio nuevo solo tiene v1, esto debe volverse configurable
por negocio en vez de un único módulo v2.

Particularidades de v2 frente a v1 (y frente a Alegra):
- Los campos de cliente/producto van por código de texto DIAN
  (identification_document_code, legal_organization_code, tribute_code,
  unit_measure_code, standard_code), no por ID numérico interno de Factus
  como en v1 — son códigos estándar iguales para cualquier cuenta.
- items.price va SIN impuestos (valor neto), a diferencia de v1 que lo pedía
  con impuestos incluidos (igual que se guarda en MyInv) — hay que quitarle
  el IVA antes de mandarlo.
- items.taxes es un array de objetos (código + tarifa), no un tax_rate plano.
- El medio de pago va en payment_details (array de objetos), no en campos
  sueltos payment_form/payment_method_code.
- Para anular una factura con nota crédito, v2 solo pide el número de
  factura (bill_number), no un ID interno como en v1 — no hace falta
  consultar la factura por separado antes de crear la nota.
- POST /v2/bills/validate crea Y timbra la factura ante la DIAN en un solo
  paso (no hay estado intermedio "abierta"), el cliente y los ítems van
  embebidos en cada factura, y no existe una API de pagos: el saldo
  pendiente / abonos de una venta a crédito se maneja 100% dentro de MyInv
  (ver Creditos/Abonos), sin sincronizar nada con Factus.
"""

import base64
import requests

BASE_URL_SANDBOX = "https://api-sandbox.factus.com.co"
BASE_URL_PRODUCCION = "https://api.factus.com.co"
# Migración a v2 validada en sandbox (factura y nota crédito) el 2026-08-18.
# Electricos.DR es el primer negocio facturando en vivo, con cuenta de
# producción real de Factus — todo MyInv apunta a producción desde aquí.
# Si en el futuro se necesita un negocio en modo pruebas, esto debe
# convertirse en un valor por negocio (columna en Usuarios) en vez de global.
BASE_URL = BASE_URL_PRODUCCION

# --- Catálogos de Factus v2 (ver Tablas de referencia de Factus v2) ---
TIPO_DOC_CODE_FACTUS = {"NIT": "31", "CC": "13", "CE": "22", "PAS": "41", "TI": "12"}  # Códigos DIAN de tipos de documento de identidad
ORGANIZACION_CODE_FACTUS = {"NIT": "1", "CC": "2"}   # Códigos de tipos de organización (1=Jurídica, 2=Natural)
TRIBUTE_CODE_CLIENTE = "ZZ"                          # No aplica (fijo, MyInv no rastrea régimen del cliente)
CODIGO_IMPUESTO_IVA = "01"                           # IVA en la tabla de códigos de impuestos
UNIT_MEASURE_CODE_DEFECTO = "94"                     # "unidad" — MyInv no distingue unidades finas
STANDARD_CODE_DEFECTO = "999"                        # Estándar de adopción del contribuyente
DOCUMENTO_FACTURA = "01"                             # Factura electrónica de venta
DOCUMENT_CODE_RANGO_FACTURA = "21"                   # Código de Factus para el rango de numeración de Factura de Venta
# Catálogo DIAN de conceptos de corrección para notas crédito (estándar,
# igual para cualquier proveedor de facturación electrónica, no cambia
# entre v1 y v2):
CORRECCION_DEVOLUCION_PARCIAL = 1               # Devolución parcial de bienes / no aceptación parcial del servicio
CORRECCION_ANULACION = 2                        # Anulación de factura electrónica
CORRECCION_REBAJA = 3                           # Rebaja o descuento parcial o total
CORRECCION_AJUSTE_PRECIO = 4                    # Ajuste de precio
CORRECCION_OTROS = 5                            # Otros
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
    doc_code = TIPO_DOC_CODE_FACTUS.get(tipo_documento)
    if not doc_code or not str(numero_documento or "").strip():
        return False, "Tipo o número de documento inválido."
    try:
        token, error = _obtener_token(client_id, client_secret, username, password)
        if not token:
            return False, f"No se pudo autenticar con Factus: {error}"
        resp = requests.get(
            f"{BASE_URL}/v2/dian/acquirer",
            headers=_headers(token),
            params={"identification_document_code": doc_code, "identification_number": str(numero_documento).strip()},
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
    una vez por cada cuenta de Factus nueva antes de poder facturar.
    document='21' es el código fijo de Factus para rango de facturación
    electrónica (factura de venta)."""
    try:
        token, error = _obtener_token(client_id, client_secret, username, password)
        if not token:
            return False, f"No se pudo autenticar con Factus: {error}"
        payload = {
            "document": DOCUMENT_CODE_RANGO_FACTURA,
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


def _resolver_numbering_range_id(token, tipo_documento="factura"):
    """numbering_range_id es obligatorio para notas crédito y opcional para
    facturas solo si la cuenta tiene un único rango activo; si hay más de
    uno, Factus lo exige y rechaza la factura/nota con un 422 sin detalle
    ('obligatorio') si no se envía, o 'inválido' si se envía uno de otro
    tipo de documento -- Factus SÍ exige un rango propio para notas crédito,
    no comparte el de la factura (confirmado en producción con v1: un rango
    con document='Factura de Venta' fue rechazado como inválido al usarlo en
    una nota crédito). Se resuelve automáticamente contra
    /v2/numbering-ranges, filtrando por el campo 'document' (nombre legible
    que devuelve Factus, ej. 'Factura de Venta' / 'Nota Crédito') de forma
    tolerante a mayúsculas/acentos; si no hay ningún rango de ese tipo, cae
    de vuelta a cualquier rango activo como último recurso. tipo_documento:
    'factura' o 'nota_credito'. Devuelve (numbering_range_id,
    rangos_crudos); rangos_crudos se devuelve siempre para poder
    diagnosticar si Factus sigue rechazando el campo."""
    try:
        resp = requests.get(f"{BASE_URL}/v2/numbering-ranges", headers=_headers(token), timeout=15)
        if resp.status_code != 200:
            return None, []
        rangos = resp.json().get("data", {}).get("data", [])

        def coincide(doc):
            d = (doc or "").lower()
            if tipo_documento == "nota_credito":
                return "nota" in d and "cr" in d
            return "factura" in d

        candidatos = [
            r for r in rangos
            if coincide(r.get("document")) and r.get("is_active") and not r.get("is_expired")
        ]
        if not candidatos:
            candidatos = [r for r in rangos if r.get("is_active") and not r.get("is_expired")]
        if not candidatos:
            return None, rangos
        candidatos.sort(key=lambda r: r.get("id", 0), reverse=True)
        return candidatos[0].get("id"), rangos
    except requests.RequestException:
        return None, []


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


def _campo(item, nombre, defecto=None):
    """Lee un campo de un renglón de venta que puede llegar como dict (armado
    a mano en Devoluciones) o como objeto (fila de Detalles_Venta)."""
    return item.get(nombre, defecto) if isinstance(item, dict) else getattr(item, nombre, defecto)


def _construir_customer(cliente):
    """Arma el objeto 'customer' embebido en la factura/nota crédito a partir
    de un Cliente de MyInv. No se envía municipality_code: la documentación
    lo marca como opcional y MyInv no rastrea el municipio DIAN de cada
    cliente. Tampoco se envía responsibilities: se deja el default de Factus
    (R-99-PN) porque MyInv no rastrea el régimen tributario del cliente."""
    tipo_doc = cliente.tipo_documento or "CC"
    customer = {
        "identification_document_code": TIPO_DOC_CODE_FACTUS.get(tipo_doc, "13"),
        "identification": cliente.documento,
        "legal_organization_code": ORGANIZACION_CODE_FACTUS.get(tipo_doc, "2"),
        "tribute_code": TRIBUTE_CODE_CLIENTE,
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
    Detalles_Venta de MyInv. precio_unitario llega con IVA incluido (como se
    guarda en MyInv) — a diferencia de v1, v2 pide items.price como valor
    neto sin impuestos, así que se le quita el IVA antes de mandarlo."""
    iva_porcentaje = float(iva_porcentaje or 0)
    cantidad = float(cantidad or 1)
    precio_unitario = float(precio_unitario or 0)
    descuento_linea = float(descuento_linea or 0)

    precio_item = precio_unitario
    discount_rate = 0.0

    if cantidad and precio_unitario and descuento_linea:
        ajuste_unitario = descuento_linea / cantidad
        if ajuste_unitario > 0:
            descuento_pct = (ajuste_unitario / precio_unitario) * 100
            if descuento_pct > 0:
                discount_rate = round(min(descuento_pct, 100), 2)
        else:
            # ajuste_unitario negativo = "aumento" (venta a un cliente más caro
            # que el precio de lista). Factus no tiene discount_rate negativo,
            # así que se factura directo al precio real cobrado en esta venta.
            precio_item = precio_unitario - ajuste_unitario

    precio_neto = precio_item / (1 + iva_porcentaje / 100) if iva_porcentaje > 0 else precio_item

    taxes = (
        [{"code": CODIGO_IMPUESTO_IVA, "rate": f"{iva_porcentaje:.2f}"}]
        if iva_porcentaje > 0
        else [{"code": CODIGO_IMPUESTO_IVA, "rate": "0.00", "is_excluded": True}]
    )

    item = {
        "code_reference": codigo_ref,
        "name": str(descripcion)[:250],
        "quantity": f"{cantidad:.2f}",
        "discount_rate": f"{discount_rate:.2f}",
        "price": f"{round(precio_neto, 2):.2f}",
        "unit_measure_code": UNIT_MEASURE_CODE_DEFECTO,
        "standard_code": STANDARD_CODE_DEFECTO,
        "taxes": taxes,
    }

    return item


def _payment_details(tipo_pago, total, fecha_vencimiento=None):
    """v2 manda el medio de pago como un array de objetos (payment_details),
    a diferencia de v1 que lo mandaba como campos sueltos del documento."""
    forma = PAYMENT_FORM_FACTUS.get(tipo_pago, "1")
    detalle = {
        "payment_form": forma,
        "payment_method_code": PAYMENT_METHOD_FACTUS.get(tipo_pago, "10") if forma == "1" else "1",
        "amount": f"{float(total or 0):.2f}",
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


def _enviar_correo_factura(token, numero_factura, email):
    """Al validar, Factus ya intenta mandar el correo solo (send_email por
    defecto en true) -- pero eso es fire-and-forget, sin ninguna confirmación
    de si realmente llegó ni forma de reintentarlo. Se llama aparte a
    POST /v2/bills/{number}/send-email para tener una respuesta explícita de
    éxito/fracaso y poder mostrarle al usuario si el envío falló."""
    try:
        resp = requests.post(
            f"{BASE_URL}/v2/bills/{numero_factura}/send-email",
            headers=_headers(token), json={"email": email}, timeout=20,
        )
        if resp.status_code in (200, 201):
            return True, None
        return False, _mensaje_error(resp)
    except requests.RequestException as e:
        return False, str(e)


def _enviar_correo_nota_credito(token, numero_nc, email):
    """Igual que _enviar_correo_factura(), pero para notas crédito."""
    try:
        resp = requests.post(
            f"{BASE_URL}/v2/credit-notes/{numero_nc}/send-email",
            headers=_headers(token), json={"email": email}, timeout=20,
        )
        if resp.status_code in (200, 201):
            return True, None
        return False, _mensaje_error(resp)
    except requests.RequestException as e:
        return False, str(e)


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
            "payment_details": _payment_details(venta.tipo_pago, venta.total, fecha_vencimiento),
            "customer": _construir_customer(cliente),
            "items": items_payload,
        }
        if getattr(venta, "notas", None):
            payload["observation"] = str(venta.notas)[:250]
        numbering_range_id, rangos_debug = _resolver_numbering_range_id(token, "factura")
        if numbering_range_id:
            payload["numbering_range_id"] = numbering_range_id

        resp = requests.post(f"{BASE_URL}/v2/bills/validate", headers=_headers(token), json=payload, timeout=30)
        if resp.status_code not in (200, 201):
            queries.guardar_resultado_factura(venta_id, estado="error")
            detalle = f"La factura fue rechazada ({resp.status_code}): {_mensaje_error(resp)}"
            detalle += f" | numbering_range_id enviado: {numbering_range_id!r} | Rangos en Factus: {rangos_debug}"
            return False, detalle

        data = resp.json().get("data", {})
        bill = data.get("bill", data)
        numero_factura = bill.get("number")
        cufe = bill.get("cufe")
        validada = bool(bill.get("is_validated", True))

        if not validada:
            queries.guardar_resultado_factura(venta_id, estado="error")
            return False, (
                "La factura se creó pero la DIAN no la validó todavía. Revisa el estado en Factus "
                "e inténtalo de nuevo (usa 'Eliminar factura no validada' desde Factus si necesitas reintentar)."
            )

        pdf_url = _pdf_base64_a_data_url(_descargar_pdf(token, numero_factura)) if numero_factura else None
        xml_url = _xml_base64_a_data_url(_descargar_xml(token, numero_factura)) if numero_factura else None

        # El send_email automático de /v2/bills/validate no confirma si el
        # correo realmente llegó -- se reintenta aquí explícito, antes de
        # guardar, para poder registrar el resultado real (no solo intentado)
        # y avisarle al usuario en el mensaje si falla, en vez de que se
        # entere solo cuando el cliente reclame que nunca le llegó la factura.
        correo_enviado, correo_error = (None, None)
        if cliente.email and numero_factura:
            correo_enviado, correo_error = _enviar_correo_factura(token, numero_factura, cliente.email)

        queries.guardar_resultado_factura(
            venta_id,
            reference_code=payload["reference_code"],
            cufe=cufe,
            pdf_url=pdf_url,
            xml_url=xml_url,
            estado="emitida",
            prefijo=None,
            numero=numero_factura,
            correo_enviado=correo_enviado,
        )

        mensaje = f"Factura {numero_factura} emitida ante la DIAN."
        if correo_enviado is False:
            mensaje += f" (No se pudo enviar el correo a {cliente.email}: {correo_error})"

        return True, mensaje
    except requests.RequestException as e:
        queries.guardar_resultado_factura(venta_id, estado="error")
        return False, f"Error de conexión al conectar con Factus para crear la factura: {e}"


def emitir_nota_credito(uid, venta_id, concepto_codigo, items_credito, motivo=None):
    """
    Emite en Factus una nota crédito que corrige una venta, según cualquiera
    de los 5 conceptos de corrección del catálogo DIAN (CORRECCION_* arriba).
    No hace nada (y no es un error) si la venta nunca tuvo factura
    electrónica emitida -- en ese caso la corrección solo vive dentro de
    MyInv (stock/total), sin nota crédito electrónica.

    items_credito: lista de objetos/dicts con producto_id, nombre_producto,
    cantidad, precio_unitario, descuento, iva_porcentaje -- el subconjunto
    (y las cantidades/montos) que realmente se está acreditando. Para una
    anulación completa es el 100% de los ítems originales a su cantidad
    completa; para una devolución parcial, solo las líneas devueltas a su
    cantidad devuelta; para rebaja/ajuste de precio/otros, las líneas
    completas con 'descuento' = monto a acreditar en esa línea (activa el
    discount_rate ya soportado por _construir_item).

    MyInv solo admite una nota crédito por venta (ver el guard de
    nota_credito_alegra_id más abajo) -- no hay notas crédito acumulativas.

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

    if not items_credito:
        return False, "No hay ítems para la nota crédito."

    items_payload = [
        _construir_item(
            _campo(item, "nombre_producto"), _campo(item, "cantidad"), _campo(item, "precio_unitario"),
            _campo(item, "descuento"), (_campo(item, "iva_porcentaje") or 0) if iva_permitido else 0,
            f"VENTA-{venta_id}-{i}",
        )
        for i, item in enumerate(items_credito, start=1)
    ]
    # Monto total acreditado (bruto, con IVA incluido, igual a como MyInv
    # guarda sus totales) -- es lo que se reporta en payment_details.amount;
    # v2 espera el bruto ahí aunque items.price vaya neto, igual que en la
    # factura original.
    total_credito = sum(
        float(_campo(item, "cantidad") or 1) * float(_campo(item, "precio_unitario") or 0)
        - float(_campo(item, "descuento") or 0)
        for item in items_credito
    )

    reference_code_nc = f"MYINV-NC-{uid}-{venta_id}"

    try:
        token, error = _obtener_token(client_id, client_secret, username, password)
        if not token:
            return False, f"No se pudo autenticar con Factus: {error}"

        payload = {
            "reference_code": reference_code_nc,
            "correction_concept_code": concepto_codigo,
            "customization_id": CUSTOMIZATION_NC_CON_FACTURA,
            "bill_number": venta.factura_numero,
            "payment_details": _payment_details(venta.tipo_pago or "Efectivo", total_credito),
            "customer": _construir_customer(cliente),
            "items": items_payload,
        }
        if motivo:
            payload["observation"] = str(motivo)[:250]
        numbering_range_id, rangos_debug = _resolver_numbering_range_id(token, "nota_credito")
        if numbering_range_id:
            payload["numbering_range_id"] = numbering_range_id

        resp = requests.post(f"{BASE_URL}/v2/credit-notes/validate", headers=_headers(token), json=payload, timeout=30)
        if resp.status_code not in (200, 201):
            detalle = f"La nota crédito fue rechazada ({resp.status_code}): {_mensaje_error(resp)}"
            detalle += f" | numbering_range_id enviado: {numbering_range_id!r} | Rangos en Factus: {rangos_debug}"
            return False, detalle

        data = resp.json().get("data", {})
        nota = data.get("credit_note", data)
        numero_nc = nota.get("number")

        pdf_url_nc = _pdf_base64_a_data_url(_descargar_pdf_nota_credito(token, numero_nc)) if numero_nc else None
        xml_url_nc = _xml_base64_a_data_url(_descargar_xml_nota_credito(token, numero_nc)) if numero_nc else None

        correo_enviado, correo_error = (None, None)
        if cliente.email and numero_nc:
            correo_enviado, correo_error = _enviar_correo_nota_credito(token, numero_nc, cliente.email)

        queries.guardar_nota_credito(
            venta_id, reference_code_nc, pdf_url=pdf_url_nc, xml_url=xml_url_nc,
            prefijo=None, numero=numero_nc,
            anular_venta=(concepto_codigo == CORRECCION_ANULACION),
            correo_enviado=correo_enviado,
        )

        mensaje = f"Nota crédito emitida ({numero_nc})."
        if correo_enviado is False:
            mensaje += f" (No se pudo enviar el correo a {cliente.email}: {correo_error})"

        return True, mensaje
    except requests.RequestException as e:
        return False, f"Error de conexión al conectar con Factus para crear la nota crédito: {e}"


def anular_factura_venta(uid, venta_id):
    """Wrapper de compatibilidad: anulación completa de una venta (concepto
    DIAN 2), referenciando el 100% de los ítems originales a su cantidad
    completa. Usado por los flujos existentes de 'Anular Venta Completa' y
    reintento de nota crédito."""
    import queries
    return emitir_nota_credito(
        uid, venta_id, CORRECCION_ANULACION, queries.obtener_items_venta(venta_id)
    )


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
