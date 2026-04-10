import os
import time

print("Nevsky worker started")
print("Environment:", os.getenv("ENVIRONMENT", "unknown"))

while True:
    time.sleep(30)
    print("Nevsky worker heartbeat")
