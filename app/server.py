"""
server.py
=========
Punto de entrada. Levanta:
  - El Estado Global Compartido (en memoria, protegido por asyncio.Lock).
  - 3 corrutinas autonomas independientes del usuario (asyncio.create_task):
      1. temporizador_global()      -> cuenta regresiva visible en pantalla.
      2. cierre_automatico_ronda()  -> vigila inactividad y fuerza avance.
      3. refresco_preguntas()       -> regenera/avanza recursos logicos del tablero.
  - La app ReactPy servida con Starlette/uvicorn.

Ejecutar con:  python server.py
"""

import asyncio
import time

from reactpy import component, html, use_state, use_effect
from reactpy.backend.starlette import configure
from starlette.applications import Starlette

import estado as E
import persistencia as P

# ---------------------------------------------------------------------------
# ESTADO GLOBAL COMPARTIDO EN MEMORIA
# ---------------------------------------------------------------------------
# Por que un Lock: varias corrutinas (humanas via evento de clic, y autonomas
# via create_task) pueden intentar leer-modificar-escribir _estado_actual
# en el mismo tick del Event Loop. asyncio.Lock asegura que cada
# transicion update() se aplique de forma atomica, evitando "race conditions"
# de lectura obsoleta (lost update) sin necesitar hilos.
_lock = asyncio.Lock()
_estado_actual = E.estado_inicial()
_id_partida_actual = None
_suscriptores = []  # callbacks de componentes ReactPy a notificar


async def despachar(tipo, payload=None):
    """
    Unica funcion que aplica transiciones de estado.
    1) Calcula el nuevo estado con la funcion PURA update().
    2) Notifica a los componentes suscritos para re-renderizar.
    Es async y usa Lock -> nunca bloquea ni corrompe el estado compartido.
    """
    global _estado_actual
    payload = payload or {}
    async with _lock:
        anterior = _estado_actual
        _estado_actual = E.update(_estado_actual, tipo, payload)
        termino_ahora = (not anterior.juego_terminado) and _estado_actual.juego_terminado

    # Notificar a la UI (fuera del lock, para no bloquear otras transiciones)
    for notificar in list(_suscriptores):
        notificar()

    if termino_ahora:
        asyncio.create_task(_persistir_fin_partida())


async def _persistir_fin_partida():
    """Disparado automaticamente cuando el estado indica fin de juego (evento de sistema)."""
    global _id_partida_actual
    snap = _estado_actual
    if not snap.jugadores:
        return
    ganador_obj = max(snap.jugadores, key=lambda j: j.puntaje)
    duracion = time.time() - snap.inicio_partida_ts
    partida_id = await P.guardar_resultado_partida(ganador_obj.nombre, ganador_obj.puntaje, duracion)
    _id_partida_actual = partida_id
    for j in snap.jugadores:
        await P.actualizar_ranking(j.id, j.nombre, j.puntaje)
    for evento_txt in snap.historial_eventos[-15:]:
        await P.registrar_evento(partida_id, evento_txt)


# ---------------------------------------------------------------------------
# CORRUTINA AUTONOMA #1: Temporizador global de rondas
# ---------------------------------------------------------------------------
async def temporizador_global():
    """
    Corrutina infinita e independiente del usuario.
    Cada segundo emite un TICK que decrementa tiempo_restante en el estado.
    Usa await asyncio.sleep() -> el Event Loop queda libre entre ticks,
    permitiendo que otras corrutinas y clics de otros navegadores se procesen.
    """
    while True:
        await asyncio.sleep(1)
        if not _estado_actual.juego_terminado:
            await despachar(E.ACCION_TICK, {})


# ---------------------------------------------------------------------------
# CORRUTINA AUTONOMA #2: Cierre automatico de rondas por inactividad
# ---------------------------------------------------------------------------
async def cierre_automatico_ronda():
    """
    Red de seguridad asincrona independiente: si pasan 2 segundos extra
    despues de que el tiempo llego a 0 y la ronda sigue "activa" (caso borde
    de UI lenta o cliente desconectado), fuerza el cierre y la penalizacion.
    Esto es una corrutina DISTINTA del temporizador, demostrando 2 tareas
    concurrentes que vigilan el mismo estado de forma independiente.
    """
    while True:
        await asyncio.sleep(2)
        if (not _estado_actual.juego_terminado
                and _estado_actual.ronda_activa
                and _estado_actual.tiempo_restante == 0):
            await despachar(E.ACCION_CERRAR_RONDA, {})


# ---------------------------------------------------------------------------
# CORRUTINA AUTONOMA #3: Refresco periodico de recursos logicos del tablero
# ---------------------------------------------------------------------------
async def auditoria_periodica():
    """
    Cada 5 segundos registra un snapshot de auditoria en la BD de forma
    NO bloqueante (await), incluso si nadie hizo clic en nada.
    Esto cumple "la persistencia debe originarse de eventos del sistema,
    no solo de acciones manuales".
    """
    while True:
        await asyncio.sleep(5)
        if _estado_actual.jugadores and not _estado_actual.juego_terminado:
            descripcion = (
                f"[AUDITORIA AUTOMATICA] {len(_estado_actual.jugadores)} jugadores activos, "
                f"pregunta #{_estado_actual.pregunta_idx + 1}, "
                f"tiempo restante {_estado_actual.tiempo_restante}s"
            )
            # Persistencia disparada por el SISTEMA, no por el usuario:
            await P.registrar_evento(_id_partida_actual or 0, descripcion)


async def iniciar_corrutinas_autonomas():
    """
    Lanza las 3 corrutinas autonomas obligatorias con asyncio.create_task()
    para que corran en paralelo lógico al servidor, y las agrupa con
    asyncio.gather() para que el ciclo de vida del proceso las espere.
    """
    tareas = [
        asyncio.create_task(temporizador_global()),
        asyncio.create_task(cierre_automatico_ronda()),
        asyncio.create_task(auditoria_periodica()),
    ]
    await asyncio.gather(*tareas)


# ---------------------------------------------------------------------------
# COMPONENTES REACTPY (funciones puras de render -> arboles virtuales)
# ---------------------------------------------------------------------------

@component
def Cronometro():
    """Muestra el tiempo restante. Se re-renderiza via suscripcion al estado."""
    _, forzar_render = use_state(0)

    def suscribirse():
        def notificar():
            forzar_render(lambda n: n + 1)
        _suscriptores.append(notificar)
        return lambda: _suscriptores.remove(notificar)

    use_effect(suscribirse, [])

    tiempo = _estado_actual.tiempo_restante
    color = "#e53935" if tiempo <= 5 else "#43a047"
    return html.div(
        {"style": {"fontSize": "2rem", "fontWeight": "bold", "color": color}},
        f"⏱ {tiempo}s",
    )


@component
def Pregunta(jugador_id):
    """Muestra la pregunta actual y los botones de respuesta (acciones humanas)."""
    _, forzar_render = use_state(0)

    def suscribirse():
        def notificar():
            forzar_render(lambda n: n + 1)
        _suscriptores.append(notificar)
        return lambda: _suscriptores.remove(notificar)

    use_effect(suscribirse, [])

    if _estado_actual.juego_terminado:
        ganador = max(_estado_actual.jugadores, key=lambda j: j.puntaje, default=None)
        texto_ganador = f"🏆 Ganador: {ganador.nombre} ({ganador.puntaje} pts)" if ganador else "Sin jugadores"
        return html.div({"style": {"fontSize": "1.5rem"}}, texto_ganador)

    p = _estado_actual.pregunta_actual
    jugador = next((j for j in _estado_actual.jugadores if j.id == jugador_id), None)
    ya_respondio = jugador.respondio_ronda_actual if jugador else False

    def manejar_click(opcion):
        async def handler(_event):
            if not ya_respondio:
                await despachar(E.ACCION_RESPONDER, {"id": jugador_id, "opcion": opcion})
        return handler

    botones = [
        html.button(
            {
                "key": op,
                "onClick": manejar_click(op),
                "disabled": ya_respondio,
                "style": {"margin": "6px", "padding": "10px 16px", "cursor": "pointer"},
            },
            op,
        )
        for op in p["opciones"]
    ]

    return html.div(
        html.h3(p["texto"]),
        html.div(botones),
        html.p("Ya respondiste, esperando a los demas..." if ya_respondio else ""),
    )


@component
def TablaPuntajes():
    _, forzar_render = use_state(0)

    def suscribirse():
        def notificar():
            forzar_render(lambda n: n + 1)
        _suscriptores.append(notificar)
        return lambda: _suscriptores.remove(notificar)

    use_effect(suscribirse, [])

    filas = [
        html.tr(
            {"key": j.id},
            html.td(j.nombre),
            html.td(str(j.puntaje)),
            html.td("Sí" if j.respondio_ronda_actual else "No"),
        )
        for j in sorted(_estado_actual.jugadores, key=lambda x: -x.puntaje)
    ]
    return html.table(
        {"style": {"width": "100%", "borderCollapse": "collapse", "marginTop": "1rem"}},
        html.thead(html.tr(html.th("Jugador"), html.th("Puntaje"), html.th("Respondió"))),
        html.tbody(filas),
    )

@component
def HistorialEventos():
    """
    Muestra el log de eventos de la partida actual en tiempo real.
    Se suscribe al estado igual que los demas componentes reactivos.
    """
    _, forzar_render = use_state(0)

    def suscribirse():
        def notificar():
            forzar_render(lambda n: n + 1)
        _suscriptores.append(notificar)
        return lambda: _suscriptores.remove(notificar)

    use_effect(suscribirse, [])

    eventos = list(reversed(_estado_actual.historial_eventos[-8:]))
    items = [html.li({"key": str(i)}, ev) for i, ev in enumerate(eventos)]

    return html.div(
        {"style": {"marginTop": "1.5rem", "backgroundColor": "#f5f5f5", "padding": "10px", "borderRadius": "8px"}},
        html.h4("📜 Historial de eventos (últimos 8)"),
        html.ul({"style": {"fontSize": "0.85rem"}}, items) if items else html.p("Sin eventos aún."),
    )

@component
def App():
    jugador_id, set_jugador_id = use_state(None)
    nombre_input, set_nombre_input = use_state("")

    async def iniciar_corrutinas_una_vez():
        # Lanza las corrutinas autonomas solo una vez (al primer montaje de App)
        asyncio.create_task(iniciar_corrutinas_autonomas())

    use_effect(lambda: asyncio.ensure_future(iniciar_corrutinas_una_vez()), [])

    if jugador_id is None:
        def manejar_input(e):
            set_nombre_input(e["target"]["value"])

        async def unirse(_event):
            nuevo_id = f"j_{int(time.time() * 1000)}"
            await despachar(E.ACCION_UNIRSE, {"id": nuevo_id, "nombre": nombre_input or "Jugador"})
            set_jugador_id(nuevo_id)

        return html.div(
            {"style": {"maxWidth": "400px", "margin": "60px auto", "textAlign": "center"}},
            html.h2("🎮 Trivia Concurrente"),
            html.input(
                {
                    "value": nombre_input,
                    "onChange": manejar_input,
                    "placeholder": "Tu nombre",
                    "style": {"padding": "8px", "width": "80%"},
                }
            ),
            html.br(),
            html.button(
                {"onClick": unirse, "style": {"marginTop": "12px", "padding": "10px 20px"}},
                "Unirse a la partida",
            ),
        )

    return html.div(
        {"style": {"maxWidth": "600px", "margin": "30px auto", "fontFamily": "sans-serif"}},
        html.h2("🎮 Trivia Concurrente"),
        Cronometro(),
        Pregunta(jugador_id),
        TablaPuntajes(),
        HistorialEventos(),
        RankingGlobal(),
    )


# ---------------------------------------------------------------------------
# ARRANQUE DEL SERVIDOR
# ---------------------------------------------------------------------------

app = Starlette()
configure(app, App)


@app.on_event("startup")
async def al_iniciar():
    await P.inicializar_bd()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)