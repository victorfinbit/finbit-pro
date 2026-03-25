import time
import subprocess
import os

while True:

    print("Actualizando Finbit...")

    # Ejecutar finbit
    subprocess.run(["py", "finbit.py"])

    # Esperar un poco para que termine de escribir
    time.sleep(2)

    print("Esperando 5 minutos...")
    time.sleep(300)