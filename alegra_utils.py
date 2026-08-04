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


def probar_conexion(email, token):
    """Verifica que un par email/token funcione contra la API de Alegra."""
    try:
        resp = requests.get(f"{BASE_URL}/contacts", headers=_headers(email, token), params={"limit": 1}, timeout=15)
        if resp.status_code == 200:
            return True, "Conexión exitosa con Alegra."
        if resp.status_code == 401:
            return False, "Credenciales rechazadas por Alegra (email o token incorrectos)."
        return False, f"Alegra respondió {resp.status_code}: {resp.text}"
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
        st.error(f"Error al crear contacto en Alegra ({resp.status_code}): {resp.text}")
        return None
    except requests.RequestException as e:
        st.error(f"Error de conexión al crear contacto: {e}")
        return None


def crear_item(email, token, nombre, precio, referencia=None):
    """Crea un ítem/producto en el catálogo de Alegra y devuelve su id, o None si falla."""
    payload = {"name": nombre, "price": precio}
    if referencia:
        payload["reference"] = referencia

    try:
        resp = requests.post(f"{BASE_URL}/items", headers=_headers(email, token), json=payload, timeout=15)
        if resp.status_code in (200, 201):
            return resp.json().get("id")
        st.error(f"Error al crear ítem en Alegra ({resp.status_code}): {resp.text}")
        return None
    except requests.RequestException as e:
        st.error(f"Error de conexión al crear ítem: {e}")
        return None


def crear_factura_venta(email, token, cliente_id, items, due_date=None, periodicity=None):
    """
    Crea una factura de venta en Alegra.
    items: lista de dicts [{"id": <id_item_alegra>, "price": float, "quantity": float}, ...]
    due_date: fecha límite de pago (str yyyy-mm-dd). Si es None, se usa hoy (pago de contado).
    periodicity: requerido por Alegra cuando la venta es a crédito ('MANUAL', 'MONTHLY', 'BIWEEKLY', etc.).
    Devuelve el JSON de la factura creada (incluye pdf/estado DIAN) o None si falla.
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

    try:
        resp = requests.post(f"{BASE_URL}/invoices", headers=_headers(email, token), json=payload, timeout=30)
        if resp.status_code in (200, 201):
            return resp.json()
        st.error(f"Error al crear factura en Alegra ({resp.status_code}): {resp.text}")
        return None
    except requests.RequestException as e:
        st.error(f"Error de conexión al crear factura: {e}")
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
        return False, f"No se pudo enviar por correo ({resp.status_code}): {resp.text}"
    except requests.RequestException as e:
        return False, f"Error de conexión al enviar correo: {e}"


def crear_nota_credito(email, token, factura_alegra_id, cliente_id, items, total):
    """
    Crea una nota crédito en Alegra que anula (total o parcialmente) una factura ya emitida.
    Devuelve el JSON de la nota crédito creada, o None si falla.
    """
    payload = {
        "date": hoy_bogota().isoformat(),
        "client": {"id": cliente_id},
        "items": items,
        "invoices": [{"id": factura_alegra_id, "amount": float(total)}],
    }

    try:
        resp = requests.post(f"{BASE_URL}/credit-notes", headers=_headers(email, token), json=payload, timeout=30)
        if resp.status_code in (200, 201):
            return resp.json()
        st.error(f"Error al crear nota crédito en Alegra ({resp.status_code}): {resp.text}")
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
        return producto.alegra_item_id

    alegra_id = crear_item(email, token, nombre=producto.nombre, precio=float(producto.precio_venta))
    if alegra_id:
        queries.guardar_alegra_item_id(producto_id, alegra_id)
    return alegra_id


PERIODICIDAD_ALEGRA = {
    "Libre": "MANUAL",
    "Semanal": "MANUAL",
    "Quincenal": "BIWEEKLY",
    "Mensual": "MONTHLY",
}


def facturar_venta(uid, venta_id):
    """
    Emite la factura electrónica de una venta ya registrada en MyInv, usando
    la cuenta de Alegra propia del negocio 'uid': crea (o reutiliza) el
    cliente y los productos en Alegra, arma la factura con los renglones de
    la venta, la crea, y guarda el resultado en Ventas.
    Devuelve (True, mensaje) o (False, mensaje).
    """
    import queries

    email, token = obtener_credenciales(uid)
    if not email or not token:
        return False, "Este negocio no tiene configurada su cuenta de Alegra. Ve a Configuración → Facturación Electrónica."

    venta = queries.obtener_venta_para_facturar(uid, venta_id)
    if not venta:
        return False, "Venta no encontrada."
    if venta.factura_alegra_id and venta.factura_estado == "emitida":
        return False, "Esta venta ya tiene una factura electrónica emitida."
    if not venta.cliente_id:
        return False, "Esta venta no tiene un cliente asociado. Selecciona un cliente registrado (con documento) para poder facturar electrónicamente."

    contacto_id = obtener_o_crear_contacto(uid, venta.cliente_id, email, token)
    if not contacto_id:
        return False, "No se pudo crear/obtener el cliente en Alegra."

    renglones = queries.obtener_items_venta(venta_id)
    items_payload = []
    for r in renglones:
        if not r.producto_id:
            return False, f"El renglón '{r.nombre_producto}' no está ligado a un producto del inventario, no se puede facturar."
        item_id = obtener_o_crear_item(uid, r.producto_id, email, token)
        if not item_id:
            return False, f"No se pudo crear/obtener el producto '{r.nombre_producto}' en Alegra."
        items_payload.append(_construir_item_payload(
            item_id, r.precio_unitario, r.cantidad, r.descuento, r.iva_porcentaje, email, token
        ))

    due_date = None
    periodicity = None
    if venta.tipo_pago == "Credito":
        credito = queries.obtener_credito_de_venta(venta_id)
        if credito and credito.fecha_limite:
            due_date = credito.fecha_limite.isoformat() if hasattr(credito.fecha_limite, "isoformat") else str(credito.fecha_limite)
            periodicity = PERIODICIDAD_ALEGRA.get(credito.tipo_cuota, "MANUAL")

    factura = crear_factura_venta(email, token, contacto_id, items_payload, due_date=due_date, periodicity=periodicity)
    if not factura:
        queries.guardar_resultado_factura(venta_id, estado="error")
        return False, "Alegra rechazó la factura. Revisa el error mostrado arriba."

    queries.guardar_resultado_factura(
        venta_id,
        alegra_id=factura.get("id"),
        cufe=factura.get("stamp", {}).get("cufe") if isinstance(factura.get("stamp"), dict) else None,
        pdf_url=factura.get("pdf") if isinstance(factura.get("pdf"), str) else None,
        estado="emitida",
    )

    mensaje = f"Factura emitida (Alegra #{factura.get('id')})."
    cliente = queries.obtener_datos_facturacion_cliente(uid, venta.cliente_id)
    if cliente and cliente.email:
        ok_mail, msg_mail = enviar_factura_email(email, token, factura.get("id"), cliente.email)
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
        return False, "Este negocio no tiene configurada su cuenta de Alegra."

    contacto_id = obtener_o_crear_contacto(uid, venta.cliente_id, email, token)
    if not contacto_id:
        return False, "No se pudo obtener el cliente en Alegra."

    renglones = queries.obtener_items_venta(venta_id)
    items_payload = []
    for r in renglones:
        if not r.producto_id:
            continue
        item_id = obtener_o_crear_item(uid, r.producto_id, email, token)
        if item_id:
            items_payload.append(_construir_item_payload(
                item_id, r.precio_unitario, r.cantidad, r.descuento, r.iva_porcentaje, email, token
            ))

    if not items_payload:
        return False, "No hay ítems válidos para la nota crédito."

    nota = crear_nota_credito(email, token, venta.factura_alegra_id, contacto_id, items_payload, venta.total)
    if not nota:
        return False, "Alegra rechazó la nota crédito. Revisa el error mostrado arriba."

    queries.guardar_nota_credito(venta_id, nota.get("id"))
    return True, f"Nota crédito emitida (Alegra #{nota.get('id')})."
