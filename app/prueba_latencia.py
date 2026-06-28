"""
prueba_latencia.py
===================
Script de evidencia para la defensa: demuestra que una operacion
"lenta" de BD (simulada con simular_latencia_io) NO bloquea el
Event Loop ni detiene otras tareas concurrentes.
Ejecutar con: python prueba_latencia.py
"""

import asyncio
import time
import persistencia as P

async def tarea_que_no_debe_detenerse():
    """Simula la UI/temporizador: imprime cada 0.5s mientras la BD 'tarda'."""
    contador = 0
    while contador < 8:
        print(f"  [UI/temporizador] sigo viva... tick {contador}")
        await asyncio.sleep(0.5)
        contador += 1


async def main():
    print("Iniciando prueba de latencia controlada (1.5s simulados en BD)...")
    inicio = time.time()

    resultados = await asyncio.gather(
        P.simular_latencia_io(1.5),       # operacion 'lenta' de BD
        tarea_que_no_debe_detenerse(),     # tarea que demuestra que el loop sigue libre
    )

    total = time.time() - inicio
    print(f"\nTiempo real de la consulta BD: {resultados[0]:.2f}s")
    print(f"Tiempo total del programa: {total:.2f}s")
    print("Si ambos tiempos son similares (~1.5-4s), confirma que las tareas")
    print("corrieron EN PARALELO LOGICO, no una despues de la otra.")


if __name__ == "__main__":
    asyncio.run(main())