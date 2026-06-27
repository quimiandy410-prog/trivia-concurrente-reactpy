"""
estado.py
=========
Define el Estado Global Compartido como una estructura INMUTABLE
(dataclasses frozen) y la función pura update(estado, accion) -> nuevo_estado.

Regla de arquitectura: ninguna función de este módulo muta el estado
recibido. Siempre se construye y retorna una instancia NUEVA.
"""

from __future__ import annotations
from dataclasses import dataclass, field, replace
from typing import Tuple, Optional
import time

# ---------------------------------------------------------------------------
# 1. PREGUNTAS DEL JUEGO (dataset estático, podría venir de BD)
# ---------------------------------------------------------------------------

PREGUNTAS: Tuple[dict, ...] = (
    {"id": 1, "texto": "¿Capital de Ecuador?", "opciones": ("Quito", "Lima", "Bogotá", "Caracas"), "correcta": "Quito"},
    {"id": 2, "texto": "¿Lenguaje creado por Guido van Rossum?", "opciones": ("Java", "Python", "C++", "Ruby"), "correcta": "Python"},
    {"id": 3, "texto": "¿Cuántos bits tiene un byte?", "opciones": ("4", "8", "16", "32"), "correcta": "8"},
    {"id": 4, "texto": "¿Quién pinta la Mona Lisa?", "opciones": ("Picasso", "Da Vinci", "Van Gogh", "Dalí"), "correcta": "Da Vinci"},
    {"id": 5, "texto": "¿Río más largo del mundo?", "opciones": ("Nilo", "Amazonas", "Misisipi", "Yangtsé"), "correcta": "Amazonas"},
)

DURACION_RONDA_SEG = 15  # tiempo límite por pregunta


# ---------------------------------------------------------------------------
# 2. ESTRUCTURAS INMUTABLES
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Jugador:
    """Representa a un jugador conectado. Inmutable: cada cambio crea otro Jugador."""
    id: str
    nombre: str
    puntaje: int = 0
    respondio_ronda_actual: bool = False
    ultima_respuesta_ms: Optional[int] = None


@dataclass(frozen=True)
class EstadoJuego:
    """
    Estado Global Compartido. TODO cambio produce una nueva instancia
    vía dataclasses.replace(), nunca se mutan los campos directamente.
    """
    jugadores: Tuple[Jugador, ...] = field(default_factory=tuple)
    pregunta_idx: int = 0
    tiempo_restante: int = DURACION_RONDA_SEG
    ronda_activa: bool = True
    juego_terminado: bool = False
    historial_eventos: Tuple[str, ...] = field(default_factory=tuple)
    inicio_partida_ts: float = field(default_factory=time.time)

    @property
    def pregunta_actual(self) -> dict:
        return PREGUNTAS[self.pregunta_idx % len(PREGUNTAS)]


def estado_inicial() -> EstadoJuego:
    return EstadoJuego()


# ---------------------------------------------------------------------------
# 3. ACCIONES (mensajes que disparan transiciones de estado)
#    Se modelan como tuplas (tipo:str, payload:dict) para simplicidad.
# ---------------------------------------------------------------------------

ACCION_UNIRSE = "UNIRSE"
ACCION_RESPONDER = "RESPONDER"
ACCION_TICK = "TICK"                 # generado por la corrutina temporizador
ACCION_CERRAR_RONDA = "CERRAR_RONDA"  # generado por inactividad/tiempo agotado
ACCION_NUEVA_PREGUNTA = "NUEVA_PREGUNTA"  # generado por corrutina de refresco
ACCION_PENALIZAR = "PENALIZAR"        # generado por corrutina de penalización


# ---------------------------------------------------------------------------
# 4. FUNCIÓN PURA DE ACTUALIZACIÓN: update(estado, accion) -> nuevo_estado
# ---------------------------------------------------------------------------

def update(estado: EstadoJuego, tipo: str, payload: dict) -> EstadoJuego:
    """
    Única puerta de entrada para transformar el estado.
    No tiene efectos secundarios: solo calcula y retorna un EstadoJuego nuevo.
    """
    if tipo == ACCION_UNIRSE:
        nuevo_jugador = Jugador(id=payload["id"], nombre=payload["nombre"])
        ya_existe = any(j.id == nuevo_jugador.id for j in estado.jugadores)
        if ya_existe:
            return estado
        evento = f"[{_hora()}] Jugador '{nuevo_jugador.nombre}' se unió a la partida."
        return replace(
            estado,
            jugadores=estado.jugadores + (nuevo_jugador,),
            historial_eventos=estado.historial_eventos + (evento,),
        )

    if tipo == ACCION_RESPONDER:
        jugador_id = payload["id"]
        opcion = payload["opcion"]
        pregunta = estado.pregunta_actual
        es_correcta = opcion == pregunta["correcta"]

        def actualizar_jugador(j: Jugador) -> Jugador:
            if j.id != jugador_id or j.respondio_ronda_actual:
                return j
            puntos = 10 if es_correcta else 0
            return replace(
                j,
                puntaje=j.puntaje + puntos,
                respondio_ronda_actual=True,
                ultima_respuesta_ms=int(time.time() * 1000),
            )

        nuevos_jugadores = tuple(actualizar_jugador(j) for j in estado.jugadores)
        evento = f"[{_hora()}] Jugador {jugador_id} respondió ({'correcto' if es_correcta else 'incorrecto'})."
        nuevo_estado = replace(
            estado,
            jugadores=nuevos_jugadores,
            historial_eventos=estado.historial_eventos + (evento,),
        )

        # Si TODOS respondieron, cerramos la ronda inmediatamente (no bloqueante,
        # solo cálculo puro; el efecto de persistencia lo hace la corrutina).
        if nuevos_jugadores and all(j.respondio_ronda_actual for j in nuevos_jugadores):
            return _avanzar_ronda(nuevo_estado)
        return nuevo_estado

    if tipo == ACCION_TICK:
        # Generado cada segundo por la corrutina temporizador_global.
        if not estado.ronda_activa or estado.juego_terminado:
            return estado
        nuevo_tiempo = max(0, estado.tiempo_restante - 1)
        if nuevo_tiempo == 0:
            return update(replace(estado, tiempo_restante=0), ACCION_CERRAR_RONDA, {})
        return replace(estado, tiempo_restante=nuevo_tiempo)

    if tipo == ACCION_CERRAR_RONDA:
        # Cierre automático por tiempo agotado (corrutina), penaliza a quien no respondió.
        evento = f"[{_hora()}] ⏰ Ronda cerrada automáticamente por tiempo agotado."
        estado_penalizado = update(estado, ACCION_PENALIZAR, {})
        return _avanzar_ronda(replace(
            estado_penalizado,
            historial_eventos=estado_penalizado.historial_eventos + (evento,),
        ))

    if tipo == ACCION_PENALIZAR:
        # Resta puntos (o simplemente marca) a jugadores que no respondieron a tiempo.
        def penalizar(j: Jugador) -> Jugador:
            if j.respondio_ronda_actual:
                return j
            return replace(j, puntaje=max(0, j.puntaje - 2))

        return replace(estado, jugadores=tuple(penalizar(j) for j in estado.jugadores))

    if tipo == ACCION_NUEVA_PREGUNTA:
        # Refresco periódico de recursos lógicos (corrutina autónoma independiente).
        siguiente = (estado.pregunta_idx + 1) % len(PREGUNTAS)
        evento = f"[{_hora()}] 🔄 Banco de preguntas refrescado automáticamente."
        return replace(
            estado,
            pregunta_idx=siguiente,
            tiempo_restante=DURACION_RONDA_SEG,
            ronda_activa=True,
            jugadores=tuple(replace(j, respondio_ronda_actual=False) for j in estado.jugadores),
            historial_eventos=estado.historial_eventos + (evento,),
        )

    # Acción desconocida: por seguridad, no se muta nada.
    return estado


def _avanzar_ronda(estado: EstadoJuego) -> EstadoJuego:
    """Transición auxiliar pura: pasa a la siguiente pregunta o termina el juego."""
    siguiente_idx = estado.pregunta_idx + 1
    if siguiente_idx >= len(PREGUNTAS):
        evento = f"[{_hora()}] 🏁 Partida finalizada."
        return replace(
            estado,
            juego_terminado=True,
            ronda_activa=False,
            historial_eventos=estado.historial_eventos + (evento,),
        )
    return replace(
        estado,
        pregunta_idx=siguiente_idx,
        tiempo_restante=DURACION_RONDA_SEG,
        ronda_activa=True,
        jugadores=tuple(replace(j, respondio_ronda_actual=False) for j in estado.jugadores),
    )


def _hora() -> str:
    return time.strftime("%H:%M:%S")