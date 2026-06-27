"""
persistencia.py
================
Capa de Persistencia No Bloqueante usando aiosqlite.

Reglas que cumple este módulo:
- TODAS las operaciones de BD son async (await) -> no bloquean el Event Loop.
- Se registra: resultado final de partida, historial de eventos, ranking global.
- La persistencia se dispara también desde eventos AUTÓNOMOS del sistema
  (no solo acciones manuales de usuario) -- ver server.py: corrutina_auditoria().
"""

import aiosqlite
import asyncio
import time

DB_PATH = "trivia.db"


async def inicializar_bd() -> None:
    """Crea las tablas si no existen. Se llama una vez al iniciar el servidor."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS partidas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha TEXT,
                duracion_seg REAL,
                ganador TEXT,
                puntaje_ganador INTEGER
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS historial_eventos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                partida_id INTEGER,
                timestamp TEXT,
                descripcion TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ranking_global (
                jugador_id TEXT PRIMARY KEY,
                nombre TEXT,
                puntaje_acumulado INTEGER DEFAULT 0,
                partidas_jugadas INTEGER DEFAULT 0
            )
        """)
        await db.commit()


async def guardar_resultado_partida(ganador: str, puntaje: int, duracion_seg: float) -> int:
    """Persiste el resultado final de una partida. No bloqueante."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO partidas (fecha, duracion_seg, ganador, puntaje_ganador) VALUES (?, ?, ?, ?)",
            (time.strftime("%Y-%m-%d %H:%M:%S"), duracion_seg, ganador, puntaje),
        )
        await db.commit()
        return cursor.lastrowid


async def registrar_evento(partida_id: int, descripcion: str) -> None:
    """Guarda una entrada de auditoría temporal. Llamado por corrutinas autónomas."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO historial_eventos (partida_id, timestamp, descripcion) VALUES (?, ?, ?)",
            (partida_id, time.strftime("%H:%M:%S"), descripcion),
        )
        await db.commit()


async def actualizar_ranking(jugador_id: str, nombre: str, puntaje_obtenido: int) -> None:
    """Actualiza el ranking global acumulado de un jugador (upsert asíncrono)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO ranking_global (jugador_id, nombre, puntaje_acumulado, partidas_jugadas)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(jugador_id) DO UPDATE SET
                puntaje_acumulado = puntaje_acumulado + ?,
                partidas_jugadas = partidas_jugadas + 1,
                nombre = ?
        """, (jugador_id, nombre, puntaje_obtenido, puntaje_obtenido, nombre))
        await db.commit()


async def obtener_ranking_top(n: int = 10) -> list:
    """Lectura asíncrona del ranking global, ordenado de mayor a menor puntaje."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT nombre, puntaje_acumulado, partidas_jugadas FROM ranking_global "
            "ORDER BY puntaje_acumulado DESC LIMIT ?", (n,)
        )
        filas = await cursor.fetchall()
        return [{"nombre": f[0], "puntaje": f[1], "partidas": f[2]} for f in filas]


async def simular_latencia_io(segundos: float = 1.5) -> None:
    """
    Prueba controlada de latencia de E/S para la defensa del proyecto.
    Demuestra que un acceso 'lento' a BD NO congela el Event Loop:
    mientras esto se ejecuta, otras corrutinas (temporizador, UI) siguen activas.
    """
    inicio = time.time()
    await asyncio.sleep(segundos)  # simula una consulta pesada, sin bloquear el loop
    return time.time() - inicio