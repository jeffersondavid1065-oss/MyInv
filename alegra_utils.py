"""
Módulo para emitir facturas electrónicas a través de la API de Alegra.
Cada negocio (usuario_id) tiene su propia cuenta de Alegra, guardada en
Usuarios.alegra_email / Usuarios.alegra_token — no hay credenciales globales.
"""

import base64
import requests
import streamlit as st
from tz_utils import hoy_bogota

BASE_URL = "https://api.alegra.com/api/v1"


def _headers(email, token):
    """Arma el header Authorization Basic a partir de las credenciales dadas."""
    credenciales = base64.b64encode(f"{email}:{token}".encode()).decode()
    return {
        "Authorization": f"Basic {credenciales}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _mensaje_error(resp):
    """Extrae el mensaje legible de una respuesta de error de la API (el
    proveedor devuelve JSON tipo {"message": "...", "code": N}); si no se
    puede parsear, cae al texto crudo para no ocultar información."""
    try:
        data = resp.json()
        if isinstance(data, dict) and data.get("message"):
            return str(data["message"])
    except ValueError:
        pass
    return resp.text


def probar_conexion(email, token):
    """Verifica que un par email/token funcione contra la API de Alegra."""
    try:
        resp = requests.get(f"{BASE_URL}/contacts", headers=_headers(email, token), params={"limit": 1}, timeout=15)
        if resp.status_code == 200:
            return True, "Conexión exitosa."
        if resp.status_code == 401:
            return False, "Credenciales rechazadas (email o token incorrectos)."
        return False, f"El proveedor respondió {resp.status_code}: {_mensaje_error(resp)}"
    except requests.RequestException as e:
        return False, f"Error de conexión: {e}"


def crear_contacto(email, token, nombre, identificacion, tipo_identificacion="CC", email_cliente=None,
                    kind_of_person="PERSON_ENTITY", regimen="SIMPLIFIED_REGIME"):
    """
    Crea un contacto/cliente en Alegra y devuelve su id, o None si falla.
    kind_of_person: 'PERSON_ENTITY' (persona natural) o 'BUSINESS_ENTITY' (empresa/NIT).
    regimen: 'SIMPLIFIED_REGIME' (régimen simplificado) o 'COMMON_REGIME' (régimen común).
    """
    partes = nombre.strip().split(" ", 1)
    first_name = partes[0]
    last_name = partes[1] if len(partes) > 1 else ""

    payload = {
        "name": nombre,
        "nameObject": {"firstName": first_name, "lastName": last_name},
        "identification": identificacion,
        "type": "client",
        "kindOfPerson": kind_of_person,
        "regime": regimen,
        "identificationObject": {"type": tipo_identificacion, "number": identificacion},
    }
    if email_cliente:
        payload["email"] = email_cliente

    try:
        resp = requests.post(f"{BASE_URL}/contacts", headers=_headers(email, token), json=payload, timeout=15)
        if resp.status_code in (200, 201):
            return resp.json().get("id")
        st.error(f"Error al crear contacto ({resp.status_code}): {_mensaje_error(resp)}")
        return None
    except requests.RequestException as e:
        st.error(f"Error de conexión al crear contacto: {e}")
        return None


# Unidad de medida de MyInv -> catálogo de Alegra/DIAN. Es obligatoria para
# poder timbrar la factura electrónica ante la DIAN ("el campo unidad de
# medida es requerido"); sin esto Alegra crea la factura pero nunca la emite.
# Las unidades de MyInv sin equivalente exacto en el catálogo de Alegra caen
# en 'unit' (unidad), el valor más genérico y seguro.
UNIDAD_MEDIDA_ALEGRA = {
    "Unidad": "unit",
    "kg": "kilogram",
    "g": "gram",
    "m": "meter",
    "cm": "centimeter",
    "L": "liter",
    "mL": "mililiter",
    "galón": "gallon",
    "Caja": "box",
    "m²": "meterSquared",
    "m³": "cubicMeter",
}


def crear_item(email, token, nombre, precio, referencia=None, unidad_medida=None):
    """Crea un ítem/producto en el catálogo de Alegra y devuelve su id, o None si falla."""
    payload = {"name": nombre, "price": precio}
    if referencia:
        payload["reference"] = referencia
    payload["inventory"] = {"unit": UNIDAD_MEDIDA_ALEGRA.get(unidad_medida, "unit")}

    try:
        resp = requests.post(f"{BASE_URL}/items", headers=_headers(email, token), json=payload, timeout=15)
        if resp.status_code in (200, 201):
            return resp.json().get("id")
        st.error(f"Error al crear ítem ({resp.status_code}): {_mensaje_error(resp)}")
        return None
    except requests.RequestException as e:
        st.error(f"Error de conexión al crear ítem: {e}")
        return None


def actualizar_unidad_item(email, token, item_id, unidad_medida):
    """
    Corrige la unidad de medida de un ítem ya existente en Alegra (creado antes
    de que MyInv empezara a enviar este campo, o si el producto cambió de
    unidad en MyInv). Best-effort: si falla, no bloquea la venta - la factura
    igual se intenta emitir y, si Alegra la rechaza por esto, el mensaje de
    error ya lo indica explícitamente.
    """
    try:
        resp = requests.put(
            f"{BASE_URL}/items/{item_id}",
            headers=_headers(email, token),
            json={"inventory": {"unit": UNIDAD_MEDIDA_ALEGRA.get(unidad_medida, "unit")}},
            timeout=15,
        )
        return resp.status_code in (200, 201)
    except requests.RequestException:
        return False


def crear_factura_venta(email, token, cliente_id, items, due_date=None, periodicity=None,
                         payment_form=None, payment_method=None):
    """
    Crea una factura de venta en Alegra.
    items: lista de dicts [{"id": <id_item_alegra>, "price": float, "quantity": float}, ...]
    due_date: fecha límite de pago (str yyyy-mm-dd). Si es None, se usa hoy (pago de contado).
    periodicity: requerido por Alegra cuando la venta es a crédito ('MANUAL', 'MONTHLY', 'BIWEEKLY', etc.).
    payment_form: 'CASH' o 'CREDIT' - obligatorio para facturación electrónica 2.1 en Colombia.
    payment_method: medio de pago (ej. 'CASH', 'DEBIT_TRANSFER_BANK') - obligatorio cuando
    payment_form es 'CASH' con facturación electrónica 2.1 activa.
    No se envía 'stamp' (timbrado): la factura queda 'abierta' en Alegra con su
    número asignado pero sin CUFE, hasta que se llame a emitir_factura_dian()
    para emitirla ante la DIAN cuando el negocio lo decida.
    Devuelve el JSON de la factura creada (incluye pdf) o None si falla.
    """
    payload = {
        "date": hoy_bogota().isoformat(),
        "dueDate": due_date or hoy_bogota().isoformat(),
        "client": {"id": cliente_id},
        "items": items,
        "status": "open",
    }
    if periodicity:
        payload["periodicity"] = periodicity
    if payment_form:
        payload["paymentForm"] = payment_form
    if payment_method:
        payload["paymentMethod"] = payment_method

    try:
        resp = requests.post(f"{BASE_URL}/invoices", headers=_headers(email, token), json=payload, timeout=30)
        if resp.status_code in (200, 201):
            return resp.json()
        st.error(f"Error al crear factura ({resp.status_code}): {_mensaje_error(resp)}")
        return None
    except requests.RequestException as e:
        st.error(f"Error de conexión al crear factura: {e}")
        return None


def emitir_factura_dian(email, token, factura_id):
    """
    Timbra ante la DIAN una factura que ya existe en Alegra en estado 'abierta'
    (creada sin generateStamp). Devuelve (True, mensaje) o (False, mensaje).
    """
    try:
        resp = requests.post(
            f"{BASE_URL}/invoices/stamp",
            headers=_headers(email, token),
            json={"ids": [int(factura_id)]},
            timeout=30,
        )
    except requests.RequestException as e:
        return False, f"Error de conexión al timbrar: {e}"

    if resp.status_code not in (200, 201):
        return False, f"El timbrado fue rechazado ({resp.status_code}): {_mensaje_error(resp)}"

    try:
        resultados = resp.json().get("data", [])
    except ValueError:
        return False, "Se recibió una respuesta inesperada al timbrar."

    resultado = next((r for r in resultados if str(r.get("id")) == str(factura_id)), None)
    if not resultado or not resultado.get("success"):
        msg = resultado.get("message") if resultado else "No se confirmó el timbrado."
        return False, msg

    return True, resultado.get("message", "Factura emitida ante la DIAN.")


def obtener_factura(email, token, factura_id):
    """Consulta el estado actual de una factura en Alegra, pidiendo explícitamente
    el PDF y el XML timbrado (el documento legal ante la DIAN): Alegra no los
    incluye por defecto, hay que pedirlos con ?fields=pdf,xml."""
    try:
        resp = requests.get(
            f"{BASE_URL}/invoices/{factura_id}", headers=_headers(email, token),
            params={"fields": "pdf,xml"}, timeout=15,
        )
        if resp.status_code == 200:
            return resp.json()
        return None
    except requests.RequestException:
        return None


def obtener_nota_credito(email, token, nota_id):
    """Consulta el estado actual de una nota crédito en Alegra, pidiendo explícitamente
    el PDF y el XML timbrado (igual que las facturas, Alegra no los incluye por defecto)."""
    try:
        resp = requests.get(
            f"{BASE_URL}/credit-notes/{nota_id}", headers=_headers(email, token),
            params={"fields": "pdf,xml"}, timeout=15,
        )
        if resp.status_code == 200:
            return resp.json()
        return None
    except requests.RequestException:
        return None


def enviar_factura_email(email, token, factura_id, destinatario):
    """Envía una factura ya emitida (estado 'open') al correo del cliente. Devuelve (True, msg) o (False, msg)."""
    try:
        resp = requests.post(
            f"{BASE_URL}/invoices/{factura_id}/email",
            headers=_headers(email, token),
            json={"emails": [destinatario]},
            timeout=20,
        )
        if resp.status_code == 200:
            return True, "Factura enviada por correo."
        return False, f"No se pudo enviar por correo ({resp.status_code}): {_mensaje_error(resp)}"
    except requests.RequestException as e:
        return False, f"Error de conexión al enviar correo: {e}"


@st.cache_data(ttl=3600)
def obtener_cuenta_por_tipo(email, token, tipo):
    """
    Busca en el catálogo de cuentas bancarias/caja de Alegra la primera que
    coincida con el tipo dado ('cash' para efectivo, 'bank' para transferencia).
    Devuelve el id, o None si no encuentra ninguna.
    """
    try:
        resp = requests.get(f"{BASE_URL}/bank-accounts", headers=_headers(email, token), params={"limit": 30}, timeout=15)
        if resp.status_code != 200:
            return None
        cuentas = resp.json()
        for c in cuentas:
            if c.get("type") == tipo:
                return c.get("id")
        return None
    except (requests.RequestException, ValueError):
        return None


def registrar_pago_factura(email, token, factura_id, contacto_id, monto, metodo_pago="cash"):
    """
    Registra un abono/pago sobre una factura ya emitida en Alegra.
    metodo_pago: 'cash' (efectivo) o 'transfer' (transferencia).
    Devuelve (True, mensaje) o (False, mensaje).
    """
    tipo_cuenta = "bank" if metodo_pago == "transfer" else "cash"
    cuenta_id = obtener_cuenta_por_tipo(email, token, tipo_cuenta)
    if not cuenta_id:
        return False, f"No se encontró una cuenta de tipo '{tipo_cuenta}' para registrar el pago."

    payload = {
        "date": hoy_bogota().isoformat(),
        "bankAccount": {"id": cuenta_id},
        "paymentMethod": metodo_pago,
        "client": {"id": contacto_id},
        "invoices": [{"id": factura_id, "amount": float(monto)}],
    }

    try:
        resp = requests.post(f"{BASE_URL}/payments", headers=_headers(email, token), json=payload, timeout=20)
        if resp.status_code in (200, 201):
            return True, "Pago registrado."
        return False, f"El pago fue rechazado ({resp.status_code}): {_mensaje_error(resp)}"
    except requests.RequestException as e:
        return False, f"Error de conexión al registrar el pago: {e}"


def crear_nota_credito(email, token, factura_alegra_id, cliente_id, items, total):
    """
    Crea una nota crédito en Alegra que anula (total o parcialmente) una factura ya emitida.
    Devuelve el JSON de la nota crédito creada, o None si falla.
    """
    payload = {
        "date": hoy_bogota().isoformat(),
        "client": {"id": cliente_id},
        "items": items,
        # 'invoiceCreditAllocations' es el campo específico de Colombia para
        # ligar la nota crédito a la factura electrónica que anula (necesario
        # para que quede asociada ante la DIAN, no solo como nota suelta).
        "invoiceCreditAllocations": [{"id": factura_alegra_id, "amount": float(total)}],
        # 'type' es el concepto/motivo de la nota crédito que exige la DIAN.
        # Como esta función siempre se usa para anular una factura electrónica
        # ya emitida (ver anular_factura_venta), el motivo es fijo.
        "type": "VOID_ELECTRONIC_INVOICE",
        # Igual que en la factura: sin esto Alegra crea la nota crédito pero
        # nunca la emite ante la DIAN.
        "stamp": {"generateStamp": True},
    }

    try:
        resp = requests.post(f"{BASE_URL}/credit-notes", headers=_headers(email, token), json=payload, timeout=30)
        if resp.status_code in (200, 201):
            return resp.json()
        st.error(f"Error al crear nota crédito ({resp.status_code}): {_mensaje_error(resp)}")
        return None
    except requests.RequestException as e:
        st.error(f"Error de conexión al crear nota crédito: {e}")
        return None


@st.cache_data(ttl=3600)
def obtener_impuesto_por_porcentaje(email, token, porcentaje):
    """
    Busca en el catálogo de impuestos de la cuenta de Alegra el id del IVA
    que coincida con ese porcentaje (ej. 19 -> id del 'IVA 19%'). Las cuentas
    colombianas de Alegra ya traen el catálogo estándar de IVA por defecto.
    Devuelve None si no encuentra uno que coincida.
    """
    try:
        resp = requests.get(f"{BASE_URL}/taxes", headers=_headers(email, token), params={"limit": 30}, timeout=15)
        if resp.status_code != 200:
            return None
        data = resp.json()
        impuestos = data.get("results", []) if isinstance(data, dict) else data
        for imp in impuestos:
            if imp.get("type") == "IVA" and abs(float(imp.get("percentage", -1)) - float(porcentaje)) < 0.01:
                return imp.get("id")
        return None
    except (requests.RequestException, ValueError, TypeError):
        return None


def _construir_item_payload(item_id, precio_unitario, cantidad, descuento_linea, iva_porcentaje, email, token):
    """
    Arma el renglón de factura para Alegra a partir de los datos guardados en
    MyInv (donde el precio ya incluye IVA): calcula el precio base sin IVA,
    el descuento como porcentaje, y referencia el impuesto correspondiente.
    """
    iva_porcentaje = float(iva_porcentaje or 0)
    precio_unitario = float(precio_unitario or 0)
    cantidad = float(cantidad or 0)
    precio_base = precio_unitario / (1 + iva_porcentaje / 100) if iva_porcentaje else precio_unitario

    payload = {
        "id": item_id,
        "price": round(precio_base, 2),
        "quantity": cantidad,
    }

    if cantidad and precio_unitario and descuento_linea:
        descuento_unitario = float(descuento_linea) / cantidad
        descuento_pct = (descuento_unitario / precio_unitario) * 100
        if descuento_pct > 0:
            payload["discount"] = round(descuento_pct, 2)

    if iva_porcentaje > 0:
        tax_id = obtener_impuesto_por_porcentaje(email, token, iva_porcentaje)
        if tax_id:
            payload["tax"] = [{"id": tax_id}]

    return payload


def obtener_credenciales(uid):
    """Devuelve (email, token) de Alegra configurados por este negocio, o (None, None) si no ha configurado nada."""
    import queries

    negocio = queries.obtener_credenciales_alegra(uid)
    if not negocio or not negocio.alegra_email or not negocio.alegra_token:
        return None, None
    return negocio.alegra_email, negocio.alegra_token


def obtener_o_crear_contacto(uid, cliente_id, email, token):
    """
    Devuelve el alegra_contact_id de un cliente de MyInv, creándolo en Alegra
    (con la cuenta del negocio 'uid') la primera vez si todavía no existe.
    """
    import queries

    cliente = queries.obtener_datos_facturacion_cliente(uid, cliente_id)
    if not cliente:
        st.error("Cliente no encontrado.")
        return None

    if cliente.alegra_contact_id:
        return cliente.alegra_contact_id

    alegra_id = crear_contacto(
        email, token,
        nombre=cliente.nombre,
        identificacion=cliente.documento,
        tipo_identificacion=cliente.tipo_documento or "CC",
        email_cliente=cliente.email,
    )
    if alegra_id:
        queries.guardar_alegra_contact_id(cliente_id, alegra_id)
    return alegra_id


def obtener_o_crear_item(uid, producto_id, email, token):
    """
    Devuelve el alegra_item_id de un producto de MyInv, creándolo en el catálogo
    de Alegra (con la cuenta del negocio 'uid') la primera vez si todavía no existe.
    """
    import queries

    producto = queries.obtener_datos_facturacion_producto(uid, producto_id)
    if not producto:
        st.error("Producto no encontrado.")
        return None

    if producto.alegra_item_id:
        actualizar_unidad_item(email, token, producto.alegra_item_id, producto.unidad_medida)
        return producto.alegra_item_id

    alegra_id = crear_item(
        email, token, nombre=producto.nombre, precio=float(producto.precio_venta),
        unidad_medida=producto.unidad_medida,
    )
    if alegra_id:
        queries.guardar_alegra_item_id(producto_id, alegra_id)
    return alegra_id


PERIODICIDAD_ALEGRA = {
    "Libre": "MANUAL",
    "Semanal": "MANUAL",
    "Quincenal": "BIWEEKLY",
    "Mensual": "MONTHLY",
}

# Forma/medio de pago de MyInv -> catálogo de Alegra (obligatorio en Colombia
# para facturación electrónica 2.1). paymentMethod solo aplica cuando
# paymentForm es 'CASH'; en 'CREDIT' Alegra no lo exige.
PAYMENT_METHOD_ALEGRA = {
    "Efectivo": "CASH",
    "Transferencia": "DEBIT_TRANSFER_BANK",
    "Mixto": "CASH",
}


def _forma_y_medio_pago(tipo_pago):
    """Traduce el tipo_pago de una venta de MyInv a (paymentForm, paymentMethod) de Alegra."""
    if tipo_pago == "Credito":
        return "CREDIT", None
    return "CASH", PAYMENT_METHOD_ALEGRA.get(tipo_pago, "CASH")


def facturar_venta(uid, venta_id):
    """
    Crea en Alegra la factura electrónica de una venta ya registrada en MyInv,
    usando la cuenta de Alegra propia del negocio 'uid': crea (o reutiliza) el
    cliente y los productos, arma la factura con los renglones de la venta y
    la crea en Alegra.
    La factura queda 'abierta' (con su número asignado) pero SIN timbrar ante
    la DIAN todavía - eso requiere un paso aparte (emitir_factura_dian_venta),
    para que el negocio pueda revisarla antes de emitirla oficialmente.
    Devuelve (True, mensaje) o (False, mensaje).
    """
    import queries

    email, token = obtener_credenciales(uid)
    if not email or not token:
        return False, "Este negocio no tiene configurada su cuenta de facturación electrónica. Ve a Configuración → Facturación Electrónica."

    venta = queries.obtener_venta_para_facturar(uid, venta_id)
    if not venta:
        return False, "Venta no encontrada."
    if venta.factura_alegra_id:
        return False, "Esta venta ya tiene una factura creada."
    if not venta.cliente_id:
        return False, "Esta venta no tiene un cliente asociado. Selecciona un cliente registrado (con documento) para poder facturar electrónicamente."

    contacto_id = obtener_o_crear_contacto(uid, venta.cliente_id, email, token)
    if not contacto_id:
        return False, "No se pudo crear/obtener el cliente."

    # Red de seguridad: si el negocio está configurado como que no declara
    # IVA, nunca se le manda IVA a la factura aunque algún producto haya
    # quedado con un % viejo - evita el rechazo del proveedor por facturar
    # con IVA a una cuenta "No responsable de IVA".
    iva_permitido = queries.tiene_iva_habilitado(uid)

    renglones = queries.obtener_items_venta(venta_id)
    items_payload = []
    for r in renglones:
        if not r.producto_id:
            return False, f"El renglón '{r.nombre_producto}' no está ligado a un producto del inventario, no se puede facturar."
        item_id = obtener_o_crear_item(uid, r.producto_id, email, token)
        if not item_id:
            return False, f"No se pudo crear/obtener el producto '{r.nombre_producto}'."
        items_payload.append(_construir_item_payload(
            item_id, r.precio_unitario, r.cantidad, r.descuento,
            r.iva_porcentaje if iva_permitido else 0, email, token
        ))

    due_date = None
    periodicity = None
    if venta.tipo_pago == "Credito":
        credito = queries.obtener_credito_de_venta(venta_id)
        if credito and credito.fecha_limite:
            due_date = credito.fecha_limite.isoformat() if hasattr(credito.fecha_limite, "isoformat") else str(credito.fecha_limite)
            periodicity = PERIODICIDAD_ALEGRA.get(credito.tipo_cuota, "MANUAL")

    payment_form, payment_method = _forma_y_medio_pago(venta.tipo_pago)
    factura = crear_factura_venta(
        email, token, contacto_id, items_payload, due_date=due_date, periodicity=periodicity,
        payment_form=payment_form, payment_method=payment_method,
    )
    if not factura:
        queries.guardar_resultado_factura(venta_id, estado="error")
        return False, "La factura fue rechazada. Revisa el error mostrado arriba."

    number_template = factura.get("numberTemplate") if isinstance(factura.get("numberTemplate"), dict) else {}
    # El PDF sí llega para una factura abierta sin timbrar; el CUFE y el XML
    # solo existen después de emitirla ante la DIAN (emitir_factura_dian_venta).
    pdf_url = factura.get("pdf") if isinstance(factura.get("pdf"), str) else None
    if not pdf_url:
        factura_completa = obtener_factura(email, token, factura.get("id"))
        if factura_completa:
            pdf_url = factura_completa.get("pdf") if isinstance(factura_completa.get("pdf"), str) else None

    queries.guardar_resultado_factura(
        venta_id,
        alegra_id=factura.get("id"),
        pdf_url=pdf_url,
        estado="abierta",
        prefijo=number_template.get("prefix"),
        numero=str(number_template["number"]) if number_template.get("number") is not None else None,
    )

    numero_texto = f"{number_template.get('prefix') or ''}{number_template.get('number') or factura.get('id')}"
    return True, f"Factura {numero_texto} creada. Pendiente de emitir ante la DIAN."


def emitir_factura_dian_venta(uid, venta_id):
    """
    Emite ante la DIAN una factura que MyInv ya creó en Alegra pero que quedó
    'abierta' (ver facturar_venta). Al emitirla completa CUFE/PDF/XML y, si el
    cliente tiene email registrado, le envía la factura por correo.
    Devuelve (True, mensaje) o (False, mensaje).
    """
    import queries

    venta = queries.obtener_venta_para_facturar(uid, venta_id)
    if not venta:
        return False, "Venta no encontrada."
    if not venta.factura_alegra_id:
        return False, "Esta venta todavía no tiene una factura creada."
    if venta.factura_estado == "emitida":
        return False, "Esta factura ya fue emitida ante la DIAN."

    email, token = obtener_credenciales(uid)
    if not email or not token:
        return False, "Este negocio no tiene configurada su cuenta de facturación electrónica."

    ok, msg = emitir_factura_dian(email, token, venta.factura_alegra_id)
    if not ok:
        return False, msg

    factura_completa = obtener_factura(email, token, venta.factura_alegra_id)
    cufe = pdf_url = xml_url = prefijo = numero = None
    if factura_completa:
        cufe = factura_completa.get("stamp", {}).get("cufe") if isinstance(factura_completa.get("stamp"), dict) else None
        pdf_url = factura_completa.get("pdf") if isinstance(factura_completa.get("pdf"), str) else None
        xml_url = factura_completa.get("xml") if isinstance(factura_completa.get("xml"), str) else None
        number_template = factura_completa.get("numberTemplate") if isinstance(factura_completa.get("numberTemplate"), dict) else {}
        prefijo = number_template.get("prefix")
        numero = str(number_template["number"]) if number_template.get("number") is not None else None

    queries.guardar_resultado_factura(
        venta_id, alegra_id=venta.factura_alegra_id, cufe=cufe, pdf_url=pdf_url, xml_url=xml_url,
        estado="emitida", prefijo=prefijo, numero=numero,
    )

    mensaje = "Factura emitida ante la DIAN."
    cliente = queries.obtener_datos_facturacion_cliente(uid, venta.cliente_id)
    if cliente and cliente.email:
        ok_mail, msg_mail = enviar_factura_email(email, token, venta.factura_alegra_id, cliente.email)
        mensaje += " Enviada por correo." if ok_mail else f" (No se pudo enviar por correo: {msg_mail})"

    return True, mensaje


def anular_factura_venta(uid, venta_id):
    """
    Emite la nota crédito en Alegra que anula la factura electrónica de una
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

    email, token = obtener_credenciales(uid)
    if not email or not token:
        return False, "Este negocio no tiene configurada su cuenta de facturación electrónica."

    contacto_id = obtener_o_crear_contacto(uid, venta.cliente_id, email, token)
    if not contacto_id:
        return False, "No se pudo obtener el cliente."

    iva_permitido = queries.tiene_iva_habilitado(uid)

    renglones = queries.obtener_items_venta(venta_id)
    items_payload = []
    for r in renglones:
        if not r.producto_id:
            continue
        item_id = obtener_o_crear_item(uid, r.producto_id, email, token)
        if item_id:
            items_payload.append(_construir_item_payload(
                item_id, r.precio_unitario, r.cantidad, r.descuento,
                r.iva_porcentaje if iva_permitido else 0, email, token
            ))

    if not items_payload:
        return False, "No hay ítems válidos para la nota crédito."

    nota = crear_nota_credito(email, token, venta.factura_alegra_id, contacto_id, items_payload, venta.total)
    if not nota:
        return False, "La nota crédito fue rechazada. Revisa el error mostrado arriba."

    pdf_url_nc = nota.get("pdf") if isinstance(nota.get("pdf"), str) else None
    xml_url_nc = nota.get("xml") if isinstance(nota.get("xml"), str) else None
    number_template_nc = nota.get("numberTemplate") if isinstance(nota.get("numberTemplate"), dict) else {}
    if not pdf_url_nc or not xml_url_nc:
        nota_completa = obtener_nota_credito(email, token, nota.get("id"))
        if nota_completa:
            if not pdf_url_nc:
                pdf_url_nc = nota_completa.get("pdf") if isinstance(nota_completa.get("pdf"), str) else None
            if not xml_url_nc:
                xml_url_nc = nota_completa.get("xml") if isinstance(nota_completa.get("xml"), str) else None
            if not number_template_nc and isinstance(nota_completa.get("numberTemplate"), dict):
                number_template_nc = nota_completa["numberTemplate"]

    queries.guardar_nota_credito(
        venta_id, nota.get("id"), pdf_url=pdf_url_nc, xml_url=xml_url_nc,
        prefijo=number_template_nc.get("prefix"),
        numero=str(number_template_nc["number"]) if number_template_nc.get("number") is not None else None,
    )
    mensaje_numero = f"{number_template_nc.get('prefix') or ''}{number_template_nc.get('number') or nota.get('id')}"
    return True, f"Nota crédito emitida ({mensaje_numero})."


def actualizar_pdf_cufe_venta(uid, venta_id):
    """
    Vuelve a consultar en Alegra la factura y/o nota crédito de una venta para
    completar el CUFE y el PDF cuando no llegaron en la respuesta de creación
    (la validación ante la DIAN puede quedar pendiente en ese momento).
    Devuelve True si completó algún dato nuevo, False si no había nada pendiente
    o Alegra todavía no tiene la validación lista.
    """
    import queries

    venta = queries.obtener_venta_para_facturar(uid, venta_id)
    if not venta:
        return False

    email, token = obtener_credenciales(uid)
    if not email or not token:
        return False

    actualizo = False

    if venta.factura_estado == "emitida" and venta.factura_alegra_id and (
        not venta.factura_cufe or not venta.factura_pdf_url or not venta.factura_xml_url
    ):
        factura = obtener_factura(email, token, venta.factura_alegra_id)
        if factura:
            cufe = factura.get("stamp", {}).get("cufe") if isinstance(factura.get("stamp"), dict) else None
            pdf_url = factura.get("pdf") if isinstance(factura.get("pdf"), str) else None
            xml_url = factura.get("xml") if isinstance(factura.get("xml"), str) else None
            number_template = factura.get("numberTemplate") if isinstance(factura.get("numberTemplate"), dict) else {}
            if cufe or pdf_url or xml_url:
                queries.actualizar_datos_factura(
                    venta_id, cufe=cufe, pdf_url=pdf_url, xml_url=xml_url,
                    prefijo=number_template.get("prefix"),
                    numero=str(number_template["number"]) if number_template.get("number") is not None else None,
                )
                actualizo = True

    if venta.nota_credito_alegra_id and (
        not venta.nota_credito_pdf_url or not venta.nota_credito_xml_url or not venta.nota_credito_numero
    ):
        nota = obtener_nota_credito(email, token, venta.nota_credito_alegra_id)
        if nota:
            pdf_url_nc = nota.get("pdf") if isinstance(nota.get("pdf"), str) else None
            xml_url_nc = nota.get("xml") if isinstance(nota.get("xml"), str) else None
            number_template_nc = nota.get("numberTemplate") if isinstance(nota.get("numberTemplate"), dict) else {}
            if pdf_url_nc or xml_url_nc or number_template_nc:
                queries.actualizar_pdf_nota_credito(
                    venta_id, pdf_url_nc, xml_url=xml_url_nc,
                    prefijo=number_template_nc.get("prefix"),
                    numero=str(number_template_nc["number"]) if number_template_nc.get("number") is not None else None,
                )
                actualizo = True

    return actualizo


@st.cache_data(ttl=300, show_spinner=False)
def _factura_cache(email, token, factura_id):
    """Envoltorio cacheado (5 min) de obtener_factura(), para poder refrescar
    enlaces automáticamente en cada render sin golpear la API de Alegra en
    cada interacción del usuario con la página."""
    return obtener_factura(email, token, factura_id)


@st.cache_data(ttl=300, show_spinner=False)
def _nota_credito_cache(email, token, nota_id):
    """Envoltorio cacheado (5 min) de obtener_nota_credito(), mismo motivo que _factura_cache()."""
    return obtener_nota_credito(email, token, nota_id)


def refrescar_url_factura(uid, venta_id):
    """
    Pide a Alegra (con caché de 5 min) un enlace fresco del PDF/XML de la
    factura de esta venta. Los enlaces que Alegra entrega son URLs firmadas de
    S3 con vencimiento (minutos/horas) - la que se guardó al crear/emitir la
    factura deja de servir después de un rato, por eso se pide una nueva cada
    vez que se va a mostrar. Solo escribe en la base de datos si el enlace
    cambió, para no invalidar la caché de consultas en cada render.
    Devuelve (pdf_url, xml_url) - el más fresco disponible, o el que ya había
    guardado si Alegra no respondió esta vez. (None, None) si no hay factura.
    """
    import queries

    venta = queries.obtener_venta_para_facturar(uid, venta_id)
    if not venta or not venta.factura_alegra_id:
        return None, None
    email, token = obtener_credenciales(uid)
    if not email or not token:
        return venta.factura_pdf_url, venta.factura_xml_url
    factura = _factura_cache(email, token, venta.factura_alegra_id)
    if not factura:
        return venta.factura_pdf_url, venta.factura_xml_url
    pdf_url = factura.get("pdf") if isinstance(factura.get("pdf"), str) else None
    xml_url = factura.get("xml") if isinstance(factura.get("xml"), str) else None
    if (pdf_url and pdf_url != venta.factura_pdf_url) or (xml_url and xml_url != venta.factura_xml_url):
        queries.actualizar_datos_factura(venta_id, pdf_url=pdf_url, xml_url=xml_url)
    return pdf_url or venta.factura_pdf_url, xml_url or venta.factura_xml_url


def refrescar_url_nota_credito(uid, venta_id):
    """Igual que refrescar_url_factura(), pero para la nota crédito de la venta."""
    import queries

    venta = queries.obtener_venta_para_facturar(uid, venta_id)
    if not venta or not venta.nota_credito_alegra_id:
        return None, None
    email, token = obtener_credenciales(uid)
    if not email or not token:
        return venta.nota_credito_pdf_url, venta.nota_credito_xml_url
    nota = _nota_credito_cache(email, token, venta.nota_credito_alegra_id)
    if not nota:
        return venta.nota_credito_pdf_url, venta.nota_credito_xml_url
    pdf_url = nota.get("pdf") if isinstance(nota.get("pdf"), str) else None
    xml_url = nota.get("xml") if isinstance(nota.get("xml"), str) else None
    if (pdf_url and pdf_url != venta.nota_credito_pdf_url) or (xml_url and xml_url != venta.nota_credito_xml_url):
        queries.actualizar_pdf_nota_credito(venta_id, pdf_url, xml_url=xml_url)
    return pdf_url or venta.nota_credito_pdf_url, xml_url or venta.nota_credito_xml_url



def registrar_abono_credito(uid, venta_id, monto, metodo_pago="Efectivo"):
    """
    Sincroniza con Alegra un abono hecho en MyInv sobre una venta a crédito:
    si esa venta tiene factura electrónica emitida, registra el pago contra
    esa factura en Alegra. Si la venta nunca se facturó, no hace nada (no es
    un error). metodo_pago: 'Efectivo' o 'Transferencia'.
    Devuelve (True, mensaje) o (False, mensaje).
    """
    import queries

    venta = queries.obtener_venta_para_facturar(uid, venta_id)
    if not venta:
        return False, "Venta no encontrada."
    if venta.factura_estado != "emitida" or not venta.factura_alegra_id:
        return True, "Esta venta no tiene factura electrónica, el abono no se sincroniza."

    email, token = obtener_credenciales(uid)
    if not email or not token:
        return False, "Este negocio no tiene configurada su cuenta de facturación electrónica."

    contacto_id = obtener_o_crear_contacto(uid, venta.cliente_id, email, token)
    if not contacto_id:
        return False, "No se pudo obtener el cliente."

    metodo_alegra = "transfer" if metodo_pago == "Transferencia" else "cash"
    return registrar_pago_factura(email, token, venta.factura_alegra_id, contacto_id, monto, metodo_alegra)
