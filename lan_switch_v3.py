#!/usr/bin/env python3
"""
Auto Switch LAN IP - Versión 4.0 MEJORADA
Soluciona falsos positivos con doble validación:
1. Pings fallidos por tiempo definido
2. Velocidad de interfaz < umbral configurado
Estrategia: NUNCA tener ambas IPs al eliminar (evita bug kernel)
"""

import subprocess
import threading
import time
import shutil
import sys
import os
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler

# ====== CONFIGURACIÓN ======
IFACE = "enP1p1s0"
IP_PRIMARY = "192.168.7.1/24"
IP_SECONDARY = "192.168.7.254/24"
PING_TARGETS = ["8.8.8.8", "1.1.1.1", "9.9.9.9", "8.8.4.4"]
PING_TIMEOUT_S = 2
CHECK_PERIOD_S = 1
FAIL_WINDOW_S = 6
UP_WINDOW_S = 6
MIN_DWELL_S = 8
ANNOUNCE_ARP_COUNT = 3

# NUEVA FUNCIONALIDAD: Verificación de velocidad
SPEED_THRESHOLD_MBPS = 10  # Megas,  Umbral mínimo de velocidad (configurable)
SPEED_CHECK_INTERVAL_S = 2  # Intervalo para medir velocidad

# Configuración de Logging
LOG_DIR = "/var/log"
LOG_FILE = f"{LOG_DIR}/lan_ip_switch.log"
LOG_MAX_SIZE = 10 * 1024 * 1024  # 10MB
LOG_BACKUP_COUNT = 5

# Delays críticos para evitar race conditions
DELAY_AFTER_DELETE = 0.5  # Espera después de eliminar IP
DELAY_AFTER_ADD = 0.5     # Espera después de agregar IP
DELAY_BEFORE_VERIFY = 0.3  # Espera antes de verificar cambios
# ===========================

def setup_logging():
    """Configura sistema de logging con más salida a pantalla"""
    logger = logging.getLogger('IPSwitch')
    logger.setLevel(logging.DEBUG)
    
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(funcName)-20s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Archivo log
    try:
        if not os.path.exists(LOG_DIR):
            os.makedirs(LOG_DIR)
        
        file_handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=LOG_MAX_SIZE,
            backupCount=LOG_BACKUP_COUNT
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        print(f"[WARN] No se pudo crear log file: {e}")
    
    # Consola - MEJORADO para más visibilidad
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)  # Cambiado de INFO a DEBUG para más detalle
    console_formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%H:%M:%S'
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_logging()

def run_cmd(cmd, timeout=5, silent=False):
    """Ejecuta comando y registra resultado"""
    cmd_str = ' '.join(cmd) if isinstance(cmd, list) else cmd
    
    if not silent:
        logger.debug(f"CMD: {cmd_str}")
    
    try:
        result = subprocess.run(
            cmd,
            shell=isinstance(cmd, str),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout
        )
        
        if not silent:
            if result.returncode != 0:
                logger.debug(f"RC={result.returncode}, ERR={result.stderr.strip()}")
        
        return result
    except subprocess.TimeoutExpired:
        logger.error(f"TIMEOUT: {cmd_str}")
        return None
    except Exception as e:
        logger.error(f"ERROR ejecutando: {e}")
        return None

def get_current_ips():
    """Obtiene las IPs actuales de la interfaz"""
    result = run_cmd(["ip", "-o", "addr", "show", "dev", IFACE], silent=True)
    if not result:
        return []
    
    ips = []
    for line in result.stdout.split('\n'):
        if 'inet ' in line:
            parts = line.split()
            try:
                inet_idx = parts.index('inet')
                if inet_idx + 1 < len(parts):
                    ips.append(parts[inet_idx + 1])
            except (ValueError, IndexError):
                pass
    
    logger.debug(f"IPs detectadas: {ips}")
    return ips

def has_ip(ip_cidr):
    """Verifica si existe una IP específica"""
    ips = get_current_ips()
    ip_only = ip_cidr.split('/')[0]
    
    for current_ip in ips:
        if current_ip == ip_cidr or current_ip.startswith(ip_only + '/'):
            return True
    return False

def flush_all_ips():
    """Elimina TODAS las IPs de la interfaz (método nuclear)"""
    logger.warning("FLUSH: Eliminando TODAS las IPs de la interfaz")
    result = run_cmd(["ip", "addr", "flush", "dev", IFACE])
    time.sleep(DELAY_AFTER_DELETE)
    return result and result.returncode == 0

def add_ip_safe(ip_cidr):
    """Agrega una IP de forma segura"""
    ip_str = ip_cidr.split('/')[0]
    
    # Verificar si ya existe
    if has_ip(ip_cidr):
        logger.info(f"IP {ip_cidr} ya existe, no se agrega")
        return True
    
    logger.info(f"Agregando IP {ip_cidr}...")
    
    # Agregar la IP
    result = run_cmd(["ip", "addr", "add", ip_cidr, "dev", IFACE])
    
    if result and result.returncode == 0:
        time.sleep(DELAY_AFTER_ADD)
        # Verificar que se agregó
        if has_ip(ip_cidr):
            logger.info(f"✓ IP {ip_cidr} agregada exitosamente")
            return True
        else:
            logger.error(f"IP {ip_cidr} no aparece después de agregar")
            return False
    elif result and "File exists" in result.stderr:
        # Ya existe según el kernel
        logger.warning(f"Kernel reporta que {ip_cidr} ya existe")
        return True
    else:
        logger.error(f"Error agregando IP: {result.stderr if result else 'Unknown'}")
        return False

def delete_ip_safe(ip_cidr):
    """Elimina una IP de forma segura (sin afectar otras)"""
    ip_str = ip_cidr.split('/')[0]
    
    # Verificar si existe
    if not has_ip(ip_cidr):
        logger.info(f"IP {ip_cidr} no existe, nada que eliminar")
        return True
    
    logger.info(f"Eliminando IP {ip_cidr}...")
    
    # IMPORTANTE: Usar la IP exacta con máscara para evitar eliminar múltiples
    result = run_cmd(["ip", "addr", "del", ip_cidr, "dev", IFACE])
    
    if result and (result.returncode == 0 or "Cannot assign" in result.stderr):
        time.sleep(DELAY_AFTER_DELETE)
        # Verificar que se eliminó
        if not has_ip(ip_cidr):
            logger.info(f"✓ IP {ip_cidr} eliminada exitosamente")
            return True
        else:
            logger.warning(f"IP {ip_cidr} sigue presente después de eliminar")
            return False
    else:
        logger.error(f"Error eliminando IP: {result.stderr if result else 'Unknown'}")
        return False

def send_arp_announce(ip_str):
    """Envía ARP gratuitous para anunciar la IP"""
    if not shutil.which("arping"):
        logger.debug("arping no disponible")
        return
    
    logger.info(f"Enviando ARP gratuitous para {ip_str}")
    
    # Probar diferentes sintaxis
    for mode in ["-U", "-A"]:
        result = run_cmd(
            ["arping", "-c", str(ANNOUNCE_ARP_COUNT), mode, "-I", IFACE, ip_str],
            timeout=3,
            silent=True
        )
        if result and result.returncode == 0:
            logger.info("✓ ARP enviado")
            return
    
    logger.debug("ARP gratuitous falló (no crítico)")

def check_interface_speed():
    """
    NUEVA FUNCIONALIDAD: Verifica la velocidad de la interfaz
    Retorna la velocidad en Mbps basada en el tráfico TX
    """
    try:
        # Obtener estadísticas iniciales
        tx_stats_path = f"/sys/class/net/{IFACE}/statistics/tx_bytes"
        
        if not os.path.exists(tx_stats_path):
            logger.error(f"No se puede acceder a estadísticas de {IFACE}")
            return -1
        
        # Primera medición
        with open(tx_stats_path, 'r') as f:
            tx0 = int(f.read().strip())
        
        # Esperar intervalo configurado
        time.sleep(SPEED_CHECK_INTERVAL_S)
        
        # Segunda medición
        with open(tx_stats_path, 'r') as f:
            tx1 = int(f.read().strip())
        
        # Calcular velocidad
        delta_bytes = tx1 - tx0
        # Convertir a Mbps: bytes -> bits (/8 -> *8) -> Mbps (/1M) -> por segundo (/interval)
        mbps = (delta_bytes * 8) / (1000 * 1000 * SPEED_CHECK_INTERVAL_S)
        
        logger.info(f"Velocidad interfaz {IFACE}: {mbps:.2f} Mbps")
        return mbps
        
    except Exception as e:
        logger.error(f"Error midiendo velocidad: {e}")
        return -1

def switch_to_secondary_safe():
    """
    Cambia a IP secundaria (.254) de forma segura
    NUEVA LÓGICA: Primero eliminar .1, luego agregar .254
    """
    logger.warning("=" * 70)
    logger.warning("CAMBIO SEGURO: PRIMARY (.1) → SECONDARY (.254)")
    logger.warning("=" * 70)
    
    # Estado inicial
    initial_ips = get_current_ips()
    logger.info(f"Estado inicial: {initial_ips}")
    
    # CRÍTICO: Verificar que no tengamos ya la .254
    # Si ya la tenemos, solo eliminar la .1
    has_1 = has_ip(IP_PRIMARY)
    has_254 = has_ip(IP_SECONDARY)
    
    logger.info(f"Verificación pre-cambio: .1={has_1}, .254={has_254}")
    
    if has_254 and not has_1:
        logger.warning("Ya estamos en estado SECONDARY, nada que hacer")
        return True
    
    if has_1 and has_254:
        logger.warning("¡ADVERTENCIA! Ambas IPs presentes - situación peligrosa")
        logger.warning("Procediendo con cuidado...")
    
    # PASO 1: Eliminar primero la IP primaria (.1)
    if has_1:
        logger.info("PASO 1: Eliminando IP primaria PRIMERO...")
        if not delete_ip_safe(IP_PRIMARY):
            # Si falla la eliminación normal, intentar flush como último recurso
            logger.warning("Eliminación normal falló, intentando método flush...")
            flush_all_ips()
            has_1 = False
            has_254 = False
        else:
            time.sleep(DELAY_BEFORE_VERIFY)
            has_1 = has_ip(IP_PRIMARY)
    
    # Verificar que .1 se eliminó
    if has_1:
        logger.error("ERROR: No se pudo eliminar .1, abortando cambio")
        return False
    
    # PASO 2: Ahora que NO hay .1, agregar .254
    logger.info("PASO 2: Agregando IP secundaria (.254)...")
    if not has_254:  # Solo si no la tenemos ya
        if not add_ip_safe(IP_SECONDARY):
            logger.error("ERROR CRÍTICO: No se pudo agregar .254")
            # Intentar recuperar al menos una IP
            logger.warning("Intentando recuperar .254...")
            time.sleep(1)
            add_ip_safe(IP_SECONDARY)
            return False
    
    # PASO 3: Verificar estado final
    time.sleep(DELAY_BEFORE_VERIFY)
    final_ips = get_current_ips()
    has_1_final = has_ip(IP_PRIMARY)
    has_254_final = has_ip(IP_SECONDARY)
    
    logger.info(f"Estado final: {final_ips}")
    logger.info(f"Verificación: .1={has_1_final}, .254={has_254_final}")
    
    # PASO 4: ARP announce (después de confirmar que tenemos .254)
    if has_254_final:
        send_arp_announce(IP_SECONDARY.split('/')[0])
    
    # Evaluación del resultado
    success = has_254_final and not has_1_final
    if success:
        logger.warning("✓✓✓ CAMBIO EXITOSO A SECONDARY (.254)")
    else:
        logger.error("✗✗✗ CAMBIO FALLIDO")
        if not has_254_final:
            logger.critical("¡NO HAY NINGUNA IP! Emergencia...")
            add_ip_safe(IP_SECONDARY)
    
    logger.warning("=" * 70)
    return success

def switch_to_primary_safe():
    """
    Restaura IP primaria (.1) de forma segura
    NUEVA LÓGICA: Primero eliminar .254, luego agregar .1
    """
    logger.warning("=" * 70)
    logger.warning("RESTAURACIÓN SEGURA: SECONDARY (.254) → PRIMARY (.1)")
    logger.warning("=" * 70)
    
    # Estado inicial
    initial_ips = get_current_ips()
    logger.info(f"Estado inicial: {initial_ips}")
    
    # Verificación pre-cambio
    has_1 = has_ip(IP_PRIMARY)
    has_254 = has_ip(IP_SECONDARY)
    
    logger.info(f"Verificación pre-cambio: .1={has_1}, .254={has_254}")
    
    if has_1 and not has_254:
        logger.warning("Ya estamos en estado PRIMARY, nada que hacer")
        return True
    
    if has_1 and has_254:
        logger.warning("¡ADVERTENCIA! Ambas IPs presentes - situación peligrosa")
        logger.warning("Procediendo con cuidado...")
    
    # PASO 1: Eliminar primero la IP secundaria (.254)
    if has_254:
        logger.info("PASO 1: Eliminando IP secundaria PRIMERO...")
        if not delete_ip_safe(IP_SECONDARY):
            # Si falla, intentar flush
            logger.warning("Eliminación normal falló, intentando método flush...")
            flush_all_ips()
            has_1 = False
            has_254 = False
        else:
            time.sleep(DELAY_BEFORE_VERIFY)
            has_254 = has_ip(IP_SECONDARY)
    
    # Verificar que .254 se eliminó
    if has_254:
        logger.error("ERROR: No se pudo eliminar .254, abortando cambio")
        return False
    
    # PASO 2: Ahora que NO hay .254, agregar .1
    logger.info("PASO 2: Agregando IP primaria (.1)...")
    if not has_1:  # Solo si no la tenemos ya
        if not add_ip_safe(IP_PRIMARY):
            logger.error("ERROR CRÍTICO: No se pudo agregar .1")
            # Intentar recuperar
            logger.warning("Intentando recuperar .1...")
            time.sleep(1)
            add_ip_safe(IP_PRIMARY)
            return False
    
    # PASO 3: Verificar estado final
    time.sleep(DELAY_BEFORE_VERIFY)
    final_ips = get_current_ips()
    has_1_final = has_ip(IP_PRIMARY)
    has_254_final = has_ip(IP_SECONDARY)
    
    logger.info(f"Estado final: {final_ips}")
    logger.info(f"Verificación: .1={has_1_final}, .254={has_254_final}")
    
    # PASO 4: ARP announce
    if has_1_final:
        send_arp_announce(IP_PRIMARY.split('/')[0])
    
    # Evaluación
    success = has_1_final and not has_254_final
    if success:
        logger.warning("✓✓✓ RESTAURACIÓN EXITOSA A PRIMARY (.1)")
    else:
        logger.error("✗✗✗ RESTAURACIÓN FALLIDA")
        if not has_1_final:
            logger.critical("¡NO HAY NINGUNA IP! Emergencia...")
            add_ip_safe(IP_PRIMARY)
    
    logger.warning("=" * 70)
    return success

def ensure_single_ip(state):
    """
    Asegura que solo tengamos UNA IP según el estado
    Maneja casos donde accidentalmente tengamos ambas IPs
    """
    ips = get_current_ips()
    has_1 = has_ip(IP_PRIMARY)
    has_254 = has_ip(IP_SECONDARY)
    
    logger.debug(f"Sanity check - Estado: {state}, .1={has_1}, .254={has_254}")
    
    # Caso 1: Ambas IPs presentes (PELIGROSO)
    if has_1 and has_254:
        logger.error("¡ALERTA! Ambas IPs presentes - corrigiendo...")
        if state == "PRIMARY":
            # Queremos .1, eliminar .254
            delete_ip_safe(IP_SECONDARY)
        else:
            # Queremos .254, eliminar .1
            delete_ip_safe(IP_PRIMARY)
        return
    
    # Caso 2: Ninguna IP (CRÍTICO)
    if not has_1 and not has_254:
        logger.error("¡EMERGENCIA! Ninguna IP presente")
        if state == "PRIMARY":
            logger.warning("Recuperando IP primaria...")
            add_ip_safe(IP_PRIMARY)
        else:
            logger.warning("Recuperando IP secundaria...")
            add_ip_safe(IP_SECONDARY)
        return
    
    # Caso 3: IP incorrecta para el estado
    if state == "PRIMARY" and has_254 and not has_1:
        logger.warning("Estado PRIMARY pero tenemos .254, corrigiendo...")
        switch_to_primary_safe()
    elif state == "SECONDARY" and has_1 and not has_254:
        logger.warning("Estado SECONDARY pero tenemos .1, corrigiendo...")
        switch_to_secondary_safe()

def ping_host(host):
    """Hace ping a un host"""
    result = run_cmd(
        ["ping", "-c", "1", "-W", str(PING_TIMEOUT_S), host],
        timeout=PING_TIMEOUT_S + 1,
        silent=True
    )
    return result and result.returncode == 0

def check_connectivity():
    """Verifica conectividad con múltiples hosts en paralelo"""
    results = {}
    threads = []
    
    def worker(host):
        results[host] = ping_host(host)
    
    for host in PING_TARGETS:
        t = threading.Thread(target=worker, args=(host,), daemon=True)
        t.start()
        threads.append(t)
    
    for t in threads:
        t.join()
    
    ok_count = sum(1 for v in results.values() if v)
    total = len(results)
    majority_ok = ok_count > (total // 2)
    
    # Log solo si hay cambios
    details = ' '.join([f"{h.split('.')[-1]}:{'✓' if results[h] else '✗'}" for h in PING_TARGETS])
    logger.debug(f"Ping: {details}")
    
    return majority_ok, ok_count, total

def verify_interface():
    """Verifica que la interfaz existe y está UP"""
    result = run_cmd(["ip", "link", "show", "dev", IFACE], silent=True)
    
    if not result or result.returncode != 0:
        logger.error(f"Interface {IFACE} no existe!")
        return False
    
    if "state UP" not in result.stdout:
        logger.warning(f"Interface {IFACE} está DOWN, levantando...")
        run_cmd(["ip", "link", "set", "dev", IFACE, "up"])
        time.sleep(2)
        # Verificar nuevamente
        result = run_cmd(["ip", "link", "show", "dev", IFACE], silent=True)
        if not result or "state UP" not in result.stdout:
            logger.error("No se pudo levantar la interface")
            return False
    
    logger.info(f"✓ Interface {IFACE} está UP")
    return True

def cleanup_initial_state():
    """
    Limpia el estado inicial de forma segura
    Asegura que solo tengamos la IP primaria
    """
    logger.info("Limpiando estado inicial...")
    
    ips = get_current_ips()
    logger.info(f"IPs encontradas: {ips}")
    
    has_1 = has_ip(IP_PRIMARY)
    has_254 = has_ip(IP_SECONDARY)
    
    # Si tenemos ambas, es peligroso - usar flush
    if has_1 and has_254:
        logger.warning("Ambas IPs presentes, limpiando todo...")
        flush_all_ips()
        time.sleep(1)
        logger.info("Re-agregando IP primaria...")
        add_ip_safe(IP_PRIMARY)
    # Si solo tenemos .254, cambiar a .1
    elif has_254 and not has_1:
        logger.info("Solo .254 presente, cambiando a .1...")
        delete_ip_safe(IP_SECONDARY)
        add_ip_safe(IP_PRIMARY)
    # Si no tenemos ninguna, agregar .1
    elif not has_1 and not has_254:
        logger.info("Sin IPs, agregando .1...")
        add_ip_safe(IP_PRIMARY)
    # Si solo tenemos .1, perfecto
    else:
        logger.info("Estado inicial correcto (.1 presente)")
    
    # Verificación final
    time.sleep(DELAY_BEFORE_VERIFY)
    final_ips = get_current_ips()
    logger.info(f"Estado inicial establecido: {final_ips}")

def main():
    """Función principal con nueva lógica de doble validación"""
    
    # Verificar root
    if os.geteuid() != 0:
        print("ERROR: Requiere sudo")
        print("Uso: sudo python3 lan_switch_v4.py")
        sys.exit(1)
    
    # Banner
    logger.warning("=" * 80)
    logger.warning("AUTO SWITCH LAN IP v4.0 - ANTI FALSOS POSITIVOS")
    logger.warning(f"Interface: {IFACE}")
    logger.warning(f"IP Primaria: {IP_PRIMARY}")
    logger.warning(f"IP Secundaria: {IP_SECONDARY}")
    logger.warning(f"Estrategia: DELETE-THEN-ADD (evita bug kernel)")
    logger.warning(f"NUEVA: Doble validación - Pings + Velocidad < {SPEED_THRESHOLD_MBPS} Mbps")
    logger.warning(f"Log: {LOG_FILE}")
    logger.warning("=" * 80)
    
    # Verificar interface
    if not verify_interface():
        logger.error("No se puede continuar sin interface válida")
        sys.exit(1)
    
    # Limpiar estado inicial
    cleanup_initial_state()
    
    # Variables de estado
    state = "PRIMARY"
    last_switch_time = time.time()
    consecutive_fail = 0
    consecutive_up = 0
    check_counter = 0
    last_sanity_check = time.time()
    
    logger.info(f"Monitoreo iniciado - Estado: {state}")
    logger.info(f"Umbral caída: {FAIL_WINDOW_S}s | Umbral recuperación: {UP_WINDOW_S}s")
    logger.info(f"Anti-flapping: {MIN_DWELL_S}s entre cambios")
    logger.info(f"Velocidad mínima: {SPEED_THRESHOLD_MBPS} Mbps")
    logger.info("-" * 80)
    
    try:
        while True:
            check_counter += 1
            current_time = time.time()
            
            # Verificar conectividad
            is_connected, ok_count, total = check_connectivity()
            
            # Actualizar contadores
            if is_connected:
                consecutive_up += 1
                consecutive_fail = 0
            else:
                consecutive_fail += 1
                consecutive_up = 0
            
            # Log periódico con más detalle
            if check_counter % 10 == 0:  # Cada 10 segundos
                status = "✓ UP" if is_connected else "✗ DOWN"
                logger.info(
                    f"Check #{check_counter} | Internet: {status} ({ok_count}/{total}) | "
                    f"Up: {consecutive_up}s Fail: {consecutive_fail}s | Estado: {state}"
                )
            
            # Sanity check cada 30 segundos
            if current_time - last_sanity_check > 30:
                ensure_single_ip(state)
                last_sanity_check = current_time
            
            # Anti-flapping
            time_since_switch = current_time - last_switch_time
            can_switch = time_since_switch >= MIN_DWELL_S
            
            # =================== NUEVA LÓGICA DE DOBLE VALIDACIÓN ===================
            if state == "PRIMARY":
                if consecutive_fail >= FAIL_WINDOW_S and can_switch:
                    logger.warning("=" * 60)
                    logger.warning(f"🔍 POSIBLE CAÍDA: Pings fallan por {FAIL_WINDOW_S} segundos")
                    logger.warning("Verificando velocidad de interfaz...")
                    
                    # NUEVA: Verificación adicional de velocidad
                    interface_speed = check_interface_speed()
                    
                    if interface_speed < 0:
                        logger.error("No se pudo medir velocidad, asumiendo falla real")
                        speed_confirms_failure = True
                    elif interface_speed < SPEED_THRESHOLD_MBPS:
                        logger.warning(f"🔴 VELOCIDAD BAJA: {interface_speed:.2f} Mbps < {SPEED_THRESHOLD_MBPS} Mbps")
                        logger.warning("✅ DOBLE VALIDACIÓN CONFIRMADA: Internet realmente caído")
                        speed_confirms_failure = True
                    else:
                        logger.info(f"🟢 VELOCIDAD OK: {interface_speed:.2f} Mbps ≥ {SPEED_THRESHOLD_MBPS} Mbps")
                        logger.warning("⚠️  FALSO POSITIVO DETECTADO: Pings fallan pero hay tráfico")
                        logger.warning("NO se cambiará la IP - evitando switcheo innecesario")
                        speed_confirms_failure = False
                        # Reset contadores para no insistir
                        consecutive_fail = 0
                    
                    if speed_confirms_failure:
                        logger.warning("🔄 PROCEDIENDO CON CAMBIO A IP SECUNDARIA...")
                        
                        if switch_to_secondary_safe():
                            state = "SECONDARY"
                            last_switch_time = current_time
                            consecutive_fail = 0
                            consecutive_up = 0
                            logger.warning("✅ CAMBIO COMPLETADO EXITOSAMENTE")
                        else:
                            logger.error("❌ CAMBIO FALLIDO, manteniendo PRIMARY")
                            ensure_single_ip("PRIMARY")
                    
                    logger.warning("=" * 60)
            
            else:  # SECONDARY
                if consecutive_up >= UP_WINDOW_S and can_switch:
                    logger.warning(f"🔄 Internet OK por {UP_WINDOW_S} segundos - restaurando PRIMARY")
                    
                    if switch_to_primary_safe():
                        state = "PRIMARY"
                        last_switch_time = current_time
                        consecutive_fail = 0
                        consecutive_up = 0
                        logger.warning("✅ RESTAURACIÓN COMPLETADA")
                    else:
                        logger.error("❌ RESTAURACIÓN FALLIDA, manteniendo SECONDARY")
                        ensure_single_ip("SECONDARY")
            # =====================================================================
            
            time.sleep(CHECK_PERIOD_S)
            
    except KeyboardInterrupt:
        logger.warning("\n" + "=" * 50)
        logger.warning("🛑 INTERRUPCIÓN MANUAL (Ctrl+C)")
        logger.warning("🔄 Restaurando IP primaria...")
        cleanup_initial_state()
        logger.warning("✅ Script terminado")
        logger.warning("=" * 50)
    except Exception as e:
        logger.critical(f"💥 ERROR NO MANEJADO: {e}", exc_info=True)
        logger.warning("🚨 Intentando dejar IP primaria activa...")
        try:
            add_ip_safe(IP_PRIMARY)
        except:
            pass
        raise

if __name__ == "__main__":
    main()
