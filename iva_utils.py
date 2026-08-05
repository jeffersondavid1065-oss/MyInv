"""
Cálculo de IVA para ventas de MyAlmacén. Los precios en Productos/carrito ya
incluyen el IVA (precio_venta con impuesto incluido), así que estas funciones
siempre parten de un valor "con IVA" y separan la base gravable del impuesto.
"""


def calcular_desglose_iva(carrito, total_final, total_bruto):
    """
    A partir del carrito (precios ya con IVA incluido), calcula el subtotal
    sin IVA y el IVA total, prorrateando el descuento global proporcionalmente.
    """
    subtotal_sin_iva = 0.0
    iva_total = 0.0
    for item in carrito:
        cant = item["cantidad"]
        if cant <= 0:
            continue
        iva_pct = item.get("iva_porcentaje", 0) or 0
        precio_neto_unit = item["subtotal"] / cant
        base_unit = precio_neto_unit / (1 + iva_pct / 100)
        subtotal_sin_iva += base_unit * cant
        iva_total += (precio_neto_unit - base_unit) * cant

    factor = (total_final / total_bruto) if total_bruto else 1.0
    return subtotal_sin_iva * factor, iva_total * factor


def calcular_iva_desde_detalles(df_detalles, total_venta):
    """Misma idea que calcular_desglose_iva, pero a partir del DataFrame de Detalles_Venta ya guardado."""
    if df_detalles is None or df_detalles.empty or 'iva_porcentaje' not in df_detalles.columns:
        return 0.0, 0.0
    subtotal_sin_iva = 0.0
    iva_total = 0.0
    suma_lineas = 0.0
    for _, row in df_detalles.iterrows():
        linea = float(row.get('subtotal', 0) or 0)
        iva_pct = float(row.get('iva_porcentaje', 0) or 0)
        base = linea / (1 + iva_pct / 100)
        subtotal_sin_iva += base
        iva_total += (linea - base)
        suma_lineas += linea
    factor = (float(total_venta) / suma_lineas) if suma_lineas else 1.0
    return subtotal_sin_iva * factor, iva_total * factor


def texto_tasa_iva(df_detalles):
    """
    Devuelve un texto representativo de la(s) tasa(s) de IVA de una venta:
    el porcentaje si todos los renglones comparten la misma tasa, "Varios"
    si hay tasas distintas mezcladas, o "0%" si no hay IVA en ningún renglón.
    """
    if df_detalles is None or df_detalles.empty or 'iva_porcentaje' not in df_detalles.columns:
        return "0%"
    tasas = sorted({float(v or 0) for v in df_detalles['iva_porcentaje']})
    if len(tasas) == 1:
        tasa = tasas[0]
        return f"{tasa:.0f}%" if tasa == int(tasa) else f"{tasa}%"
    return "Varios"
