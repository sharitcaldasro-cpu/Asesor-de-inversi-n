#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
Proyecto de Aula — Mercado de Capitales — UNIPAZ (Barrancabermeja, Colombia)
Script: actualizar_datos.py (Python 3.12)
Descripción:
    Descarga precios históricos diarios (3 años) y dividendos de acciones
    colombianas desde Yahoo Finance (yfinance), así como la TRM (USDCOP=X),
    calcula los indicadores financieros requeridos y genera el archivo
    'datos.json' para ser consumido por 'index.html'.
================================================================================
"""



# ==============================================================================
# 1. CONFIGURACIÓN EDITABLE DE ACCIONES Y ARCHIVO DE SALIDA
# ==============================================================================
ARCHIVO_SALIDA = Path(__file__).resolve().parent / "datos.json"

# Lista de acciones: (ticker_yahoo, nombre_empresa, nemotecnico_bvc, sector)
ACCIONES_CONFIG: list[tuple[str, str, str, str]] = [
    ("ECOPETROL.CL", "Ecopetrol", "ECOPETROL", "Petróleo y gas"),
    ("PFCIBEST.CL", "Grupo Cibest preferencial", "PFCIBEST", "Financiero"),
    ("CIBEST.CL", "Grupo Cibest ordinaria", "CIBEST", "Financiero"),
    ("ISA.CL", "ISA", "ISA", "Energía e infraestructura"),
    ("EXITO.CL", "Grupo Éxito", "EXITO", "Comercio"),
    ("PFAVAL.CL", "Grupo Aval preferencial", "PFAVAL", "Financiero"),
    ("GEB.CL", "Grupo Energía Bogotá", "GEB", "Energía"),
    ("CELSIA.CL", "Celsia", "CELSIA", "Energía"),
]

TICKER_TRM = "USDCOP=X"
FUENTE_TEXTO = (
    "Yahoo Finance (yfinance); rentabilidades con precio ajustado por dividendos"
)


# ==============================================================================
# 2. FUNCIONES AUXILIARES DE CÁLCULO FINANCIERO
# ==============================================================================
def normalizar_indice_fechas(df: pd.DataFrame) -> pd.DataFrame:
    """Elimina la zona horaria del índice de fechas y ordena cronológicamente."""
    df = df.copy()
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
    df.index = pd.to_datetime(df.index).normalize()
    return df.sort_index()


def obtener_trm() -> dict[str, Any] | None:
    """
    Intenta descargar el último cierre de la TRM (USDCOP=X).
    Si falla o no hay datos válidos, retorna None (se serializa como null).
    """
    try:
        hist = yf.Ticker(TICKER_TRM).history(period="10d", auto_adjust=False)
        if hist is None or hist.empty or "Close" not in hist.columns:
            return None

        hist = normalizar_indice_fechas(hist)
        cierres = hist["Close"].dropna()
        if cierres.empty:
            return None

        ultimo_valor = float(cierres.iloc[-1])
        ultima_fecha = cierres.index[-1].strftime("%Y-%m-%d")

        if math.isnan(ultimo_valor) or ultimo_valor <= 0:
            return None

        return {
            "valor": round(ultimo_valor, 2),
            "fecha": ultima_fecha,
        }
    except Exception as exc:
        print(f"[AVISO] No se pudo obtener la TRM ({TICKER_TRM}): {exc}")
        return None


def procesar_accion(
    ticker: str,
    nombre: str,
    nemotecnico: str,
    sector: str,
) -> dict[str, Any]:
    """
    Descarga 3 años de datos diarios para un ticker y calcula todas las
    métricas financieras exigidas por el aplicativo.
    """
    hist = yf.Ticker(ticker).history(period="3y", auto_adjust=False)

    if hist is None or hist.empty:
        raise ValueError("Yahoo Finance devolvió una tabla vacía.")

    columnas_requeridas = {"Close", "Adj Close"}
    if not columnas_requeridas.issubset(set(hist.columns)):
        raise ValueError(
            f"Faltan columnas requeridas. Disponibles: {list(hist.columns)}"
        )

    hist = normalizar_indice_fechas(hist)

    # Filtrar filas con Close y Adj Close válidos
    df_valido = hist[["Close", "Adj Close"]].dropna()
    if len(df_valido) < 2:
        raise ValueError("Datos insuficientes (menos de 2 jornadas válidas).")

    # 1. Precio actual y fecha de cierre
    precio = float(df_valido["Close"].iloc[-1])
    precio_anterior = float(df_valido["Close"].iloc[-2])
    fecha_ultima = df_valido.index[-1]
    fecha_primera = df_valido.index[0]

    if precio <= 0 or precio_anterior <= 0:
        raise ValueError("Precios de cierre inválidos (menores o iguales a cero).")

    # 2. Variación del día (%)
    variacion_dia_pct = ((precio / precio_anterior) - 1.0) * 100.0

    # 3. Rentabilidad 1 año (Adj Close último / Adj Close de hace 1 año más cercano - 1)
    adj_ultimo = float(df_valido["Adj Close"].iloc[-1])
    fecha_objetivo_1a = fecha_ultima - timedelta(days=365)

    # Buscar el índice con la distancia temporal mínima a hace 365 días
    diferencias_dias = (df_valido.index - fecha_objetivo_1a).to_series().abs()
    fecha_mas_cercana_1a = diferencias_dias.idxmin()
    adj_hace_1a = float(df_valido.loc[fecha_mas_cercana_1a, "Adj Close"])

    if adj_hace_1a <= 0:
        raise ValueError("Adj Close de hace 1 año inválido.")

    rent_1a_pct = ((adj_ultimo / adj_hace_1a) - 1.0) * 100.0

    # 4. Rentabilidad anual compuesta 3 años (CAGR con fechas reales)
    adj_primero = float(df_valido["Adj Close"].iloc[0])
    dias_transcurridos = (fecha_ultima - fecha_primera).days
    anios_transcurridos = dias_transcurridos / 365.25

    if adj_primero <= 0 or anios_transcurridos <= 0:
        raise ValueError("Rango de fechas insuficiente para calcular CAGR a 3 años.")

    rent_anual_3a_pct = (
        (adj_ultimo / adj_primero) ** (1.0 / anios_transcurridos) - 1.0
    ) * 100.0

    # Filtrar subconjunto del último año (365 días calendario) para volatilidad, drawdown y dividendos
    df_ultimo_anio = df_valido[df_valido.index >= fecha_objetivo_1a]
    if len(df_ultimo_anio) < 2:
        df_ultimo_anio = df_valido

    # 5. Volatilidad anual (%) = std(log_returns diarios 1a) * sqrt(252)
    serie_adj_1a = df_ultimo_anio["Adj Close"]
    ratios = serie_adj_1a / serie_adj_1a.shift(1)
    ratios = ratios.dropna()
    ratios = ratios[ratios > 0]

    if len(ratios) >= 2:
        rendimientos_log = ratios.apply(math.log)
        volatilidad_anual_pct = float(rendimientos_log.std(ddof=1)) * math.sqrt(252) * 100.0
    else:
        volatilidad_anual_pct = 0.0

    # 6. Caída máxima en 1 año (Maximum Drawdown, valor negativo en %)
    maximo_acumulado = serie_adj_1a.cummax()
    drawdowns = (serie_adj_1a / maximo_acumulado) - 1.0
    caida_maxima_1a_pct = float(drawdowns.min()) * 100.0
    if caida_maxima_1a_pct > 0:
        caida_maxima_1a_pct = 0.0

    # 7. Rendimiento por dividendo (%) = suma Dividends últimos 365 días / precio
    if "Dividends" in hist.columns:
        div_series = hist["Dividends"].fillna(0.0)
        div_ultimo_anio = div_series[div_series.index >= fecha_objetivo_1a]
        suma_dividendos = float(div_ultimo_anio.sum())
    else:
        suma_dividendos = 0.0

    dividendo_rend_pct = (suma_dividendos / precio) * 100.0

    # 8. Histórico mensual (último Close de cada mes de los 3 años)
    cierre_mensual = df_valido["Close"].resample("ME").last().dropna()
    historico_mensual = [
        {
            "mes": fecha_mes.strftime("%Y-%m"),
            "cierre": round(float(valor_cierre), 2),
        }
        for fecha_mes, valor_cierre in cierre_mensual.items()
    ]

    return {
        "ticker": ticker,
        "nombre": nombre,
        "nemotecnico": nemotecnico,
        "sector": sector,
        "moneda": "COP",
        "precio": round(precio, 2),
        "fecha": fecha_ultima.strftime("%Y-%m-%d"),
        "variacion_dia_pct": round(variacion_dia_pct, 2),
        "rent_1a_pct": round(rent_1a_pct, 2),
        "rent_anual_3a_pct": round(rent_anual_3a_pct, 2),
        "volatilidad_anual_pct": round(volatilidad_anual_pct, 2),
        "caida_maxima_1a_pct": round(caida_maxima_1a_pct, 2),
        "dividendo_rend_pct": round(dividendo_rend_pct, 2),
        "historico": historico_mensual,
    }


# ==============================================================================
# 3. EJECUCIÓN PRINCIPAL CON MANEJO ROBUSTO DE ERRORES
# ==============================================================================
def main() -> int:
    print("=" * 72)
    print("RUTA CAPITAL DE ASESORES DE INVERSIÓN — ACTUALIZADOR DE DATOS (UNIPAZ)")
    print("=" * 72)

    # 1. Descargar TRM
    print(f"Consultando TRM ({TICKER_TRM})...")
    trm_data = obtener_trm()
    if trm_data:
        print(f"  -> TRM obtenida: $ {trm_data['valor']} ({trm_data['fecha']})")
    else:
        print("  -> TRM no disponible (se guardará como null).")

    # 2. Descargar y procesar cada acción sin detener el flujo ante fallos
    acciones_exitosas: list[dict[str, Any]] = []
    errores: list[dict[str, str]] = []

    for ticker, nombre, nemotecnico, sector in ACCIONES_CONFIG:
        print(f"Descargando {ticker} ({nombre})...")
        try:
            datos_accion = procesar_accion(ticker, nombre, nemotecnico, sector)
            acciones_exitosas.append(datos_accion)
            print(
                f"  -> OK | Precio: {datos_accion['precio']} | "
                f"Rent 3a: {datos_accion['rent_anual_3a_pct']} % | "
                f"Vol: {datos_accion['volatilidad_anual_pct']} %"
            )
        except Exception as exc:
            motivo = str(exc).strip() or exc.__class__.__name__
            errores.append({"ticker": ticker, "motivo": motivo})
            print(f"  -> ERROR en {ticker}: {motivo}")

    # 3. Validación crítica: si TODAS fallan, conservar el datos.json anterior
    print("-" * 72)
    if len(acciones_exitosas) == 0:
        print(
            "[ADVERTENCIA CRÍTICA] Ninguna acción pudo actualizarse con éxito. "
            "No se sobrescribirá 'datos.json' para conservar los datos anteriores."
        )
        print(f"Total actualizadas: 0 | Total fallidas: {len(errores)}")
        for err in errores:
            print(f"  - {err['ticker']}: {err['motivo']}")
        return 0

    # 4. Construir estructura exacta de datos.json y guardar en disco
    timestamp_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "actualizado": timestamp_utc,
        "fuente": FUENTE_TEXTO,
        "trm": trm_data,
        "acciones": acciones_exitosas,
        "errores": errores,
    }

    try:
        with open(ARCHIVO_SALIDA, "w", encoding="utf-8") as archivo:
            json.dump(payload, archivo, ensure_ascii=False, indent=2)
        print(f"Archivo guardado correctamente en: {ARCHIVO_SALIDA}")
    except Exception as exc:
        print(f"[ERROR DE ESCRITURA] No se pudo guardar datos.json: {exc}")

    # 5. Resumen final
    print("-" * 72)
    print(
        f"RESUMEN FINAL: {len(acciones_exitosas)} acciones actualizadas · "
        f"{len(errores)} fallidas."
    )
    if errores:
        print("Detalle de acciones con error:")
        for err in errores:
            print(f"  * {err['ticker']}: {err['motivo']}")
    else:
        print("Todas las acciones se actualizaron sin errores.")
    print("=" * 72)

    return 0


if __name__ == "__main__":
    sys.exit(main())
