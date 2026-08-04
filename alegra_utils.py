"""
Módulo para emitir facturas electrónicas a través de la API de Alegra.
Fase de desarrollo: usa una sola cuenta (la del sandbox) desde st.secrets.
Cuando se conecte por negocio (multi-tenant), las funciones deberán recibir
email/token en vez de leerlos siempre de st.secrets.
"""

import base64
import requests
import streamlit as st
from tz_utils import hoy_bogota

BASE_URL = "https://api.alegra.com/api/v1"


def _headers():
    """Arma el header Authorization Basic a partir de las credenciales en secrets."""
    email = st.secrets["alegra"]["email"]
    token = st.secrets["alegra"]["token"]
    credenciales = base64.b64encode(f"{email}:{token}".encode()).decode()
    return {
        "Authorization": f"Basic {credenciales}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def probar_conexion():
    """Verifica que las credenciales configuradas funcionen contra la API de Alegra."""
    try:
        resp = requests.get(f"{BASE_URL}/contacts", headers=_headers(), params={"limit": 1}, timeout=15)
        if resp.status_code == 200:
            return True, "Conexión exitosa con Alegra."
        return False, f"Alegra respondió {resp.status_code}: {resp.text}"
    except requests.RequestException as e:
        return False, f"Error de conexión: {e}"


def crear_contacto(nombre, identificacion, tipo_identificacion="CC", email=None,
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
    if email:
        payload["email"] = email

    try:
        resp = requests.post(f"{BASE_URL}/contacts", headers=_headers(), json=payload, timeout=15)
        if resp.status_code in (200, 201):
            return resp.json().get("id")
        st.error(f"Error al crear contacto en Alegra ({resp.status_code}): {resp.text}")
        return None
    except requests.RequestException as e:
        st.error(f"Error de conexión al crear contacto: {e}")
        return None


def crear_item(nombre, precio, referencia=None):
    """Crea un ítem/producto en el catálogo de Alegra y devuelve su id, o None si falla."""
    payload = {"name": nombre, "price": precio}
    if referencia:
        payload["reference"] = referencia

    try:
        resp = requests.post(f"{BASE_URL}/items", headers=_headers(), json=payload, timeout=15)
        if resp.status_code in (200, 201):
            return resp.json().get("id")
        st.error(f"Error al crear ítem en Alegra ({resp.status_code}): {resp.text}")
        return None
    except requests.RequestException as e:
        st.error(f"Error de conexión al crear ítem: {e}")
        return None


def obtener_o_crear_contacto(uid, cliente_id):
    """
    Devuelve el alegra_contact_id de un cliente de MyInv, creándolo en Alegra
    la primera vez (y guardándolo) si todavía no existe.
    """
    import queries

    cliente = queries.obtener_datos_facturacion_cliente(uid, cliente_id)
    if not cliente:
        st.error("Cliente no encontrado.")
        return None

    if cliente.alegra_contact_id:
        return cliente.alegra_contact_id

    alegra_id = crear_contacto(
        nombre=cliente.nombre,
        identificacion=cliente.documento,
        tipo_identificacion=cliente.tipo_documento or "CC",
        email=cliente.email,
    )
    if alegra_id:
        queries.guardar_alegra_contact_id(cliente_id, alegra_id)
    return alegra_id


def obtener_o_crear_item(uid, producto_id):
    """
    Devuelve el alegra_item_id de un producto de MyInv, creándolo en el catálogo
    de Alegra la primera vez (y guardándolo) si todavía no existe.
    """
    import queries

    producto = queries.obtener_datos_facturacion_producto(uid, producto_id)
    if not producto:
        st.error("Producto no encontrado.")
        return None

    if producto.alegra_item_id:
        return producto.alegra_item_id

    alegra_id = crear_item(nombre=producto.nombre, precio=float(producto.precio_venta))
    if alegra_id:
        queries.guardar_alegra_item_id(producto_id, alegra_id)
    return alegra_id


def crear_factura_venta(cliente_id, items):
    """
    Crea una factura de venta en Alegra.
    items: lista de dicts [{"id": <id_item_alegra>, "price": float, "quantity": float}, ...]
    Devuelve el JSON de la factura creada (incluye pdf/estado DIAN) o None si falla.
    """
    payload = {
        "date": hoy_bogota().isoformat(),
        "dueDate": hoy_bogota().isoformat(),
        "client": {"id": cliente_id},
        "items": items,
    }

    try:
        resp = requests.post(f"{BASE_URL}/invoices", headers=_headers(), json=payload, timeout=30)
        if resp.status_code in (200, 201):
            return resp.json()
        st.error(f"Error al crear factura en Alegra ({resp.status_code}): {resp.text}")
        return None
    except requests.RequestException as e:
        st.error(f"Error de conexión al crear factura: {e}")
        return None


def facturar_venta(uid, venta_id):
    """
    Emite la factura electrónica de una venta ya registrada en MyInv:
    crea (o reutiliza) el cliente y los productos en Alegra, arma la factura
    con los renglones de la venta, la crea, y guarda el resultado en Ventas.
    Devuelve (True, mensaje) o (False, mensaje).
    """
    import queries

    venta = queries.obtener_venta_para_facturar(uid, venta_id)
    if not venta:
        return False, "Venta no encontrada."
    if venta.factura_alegra_id and venta.factura_estado == "emitida":
        return False, "Esta venta ya tiene una factura electrónica emitida."
    if not venta.cliente_id:
        return False, "Esta venta no tiene un cliente asociado. Selecciona un cliente registrado (con documento) para poder facturar electrónicamente."

    contacto_id = obtener_o_crear_contacto(uid, venta.cliente_id)
    if not contacto_id:
        return False, "No se pudo crear/obtener el cliente en Alegra."

    renglones = queries.obtener_items_venta(venta_id)
    items_payload = []
    for r in renglones:
        if not r.producto_id:
            return False, f"El renglón '{r.nombre_producto}' no está ligado a un producto del inventario, no se puede facturar."
        item_id = obtener_o_crear_item(uid, r.producto_id)
        if not item_id:
            return False, f"No se pudo crear/obtener el producto '{r.nombre_producto}' en Alegra."
        items_payload.append({
            "id": item_id,
            "price": float(r.precio_unitario),
            "quantity": float(r.cantidad),
        })

    factura = crear_factura_venta(contacto_id, items_payload)
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
    return True, f"Factura emitida (Alegra #{factura.get('id')})."
