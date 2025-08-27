# Auto Switch LAN IP para Orange Pi

> Script avanzado en Python para cambiar automáticamente la IP LAN de una interfaz en Orange Pi, con doble validación anti-falsos positivos.

## Descripción

Este script monitoriza la conectividad a Internet y la velocidad de la interfaz LAN. Si detecta caída real (pings fallidos y velocidad baja), cambia de la IP primaria a la secundaria de forma segura, evitando bugs del kernel y falsos positivos. También restaura la IP primaria cuando la conectividad vuelve.

## Características principales

- Doble validación: solo cambia IP si fallan los pings **y** la velocidad de la interfaz es baja
- Estrategia segura: nunca deja ambas IPs activas simultáneamente
- Anuncio ARP automático tras cada cambio de IP
- Logging detallado a archivo y consola
- Anti-flapping: evita cambios rápidos e innecesarios
- Recuperación automática ante estados anómalos

## Requisitos

- Python 3.8+
- Orange Pi (o cualquier Linux con interfaz de red compatible)
- Permisos de root (sudo)
- Utilidades: `ip`, `arping`, `ping`

## Instalación

```bash
git clone https://github.com/ubntomar/orangepi-switch-lan-ip.git
cd orangepi-switch-lan-ip
# Instala dependencias del sistema si es necesario:
sudo apt install iputils-arping iproute2
# No requiere dependencias Python externas
```

## Uso

```bash
sudo python3 lan_switch_v3.py
```

El script debe ejecutarse como root. Monitorea la conectividad y cambia la IP de la interfaz configurada automáticamente.

## Configuración rápida

Edita las variables al inicio de `lan_switch_v3.py` para ajustar:

- `IFACE`: nombre de la interfaz LAN (ej: enP1p1s0)
- `IP_PRIMARY` y `IP_SECONDARY`: IPs a alternar
- `PING_TARGETS`: hosts a los que se hace ping
- `SPEED_THRESHOLD_MBPS`: umbral mínimo de velocidad para considerar caída real

## Logs

El log detallado se guarda en `/var/log/lan_ip_switch.log` y también se muestra por consola.

## Seguridad

El script nunca deja ambas IPs activas a la vez y recupera automáticamente la IP primaria ante errores o interrupciones.

## Autor

ubntomar

---
¡Contribuciones y mejoras bienvenidas!

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

## License

This project is licensed under the MIT License - see the LICENSE file for details.