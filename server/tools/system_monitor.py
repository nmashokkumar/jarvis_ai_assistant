"""
System hardware and service monitor tool for JARVIS.
Collects real-time CPU, RAM, disk, uptime, and service metrics via psutil.
"""
from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)


def _format_bytes(bytes_num: int | float) -> str:
    """Format bytes into human-readable GB/MB."""
    gb = bytes_num / (1024 ** 3)
    if gb >= 1.0:
        return f"{gb:.1f} GB"
    mb = bytes_num / (1024 ** 2)
    return f"{mb:.1f} MB"


def _format_uptime(seconds: float) -> str:
    """Format uptime seconds into days, hours, minutes."""
    days, remainder = divmod(int(seconds), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def get_system_status(aspect: str = "all") -> str:
    """
    Synchronously gather system metrics.
    Runs fast without blocking the event loop.
    """
    try:
        import psutil
    except ImportError:
        return "psutil is not installed on this server."

    try:
        cpu_percent = psutil.cpu_percent(interval=0.1)
        cpu_count = psutil.cpu_count(logical=True)
        mem = psutil.virtual_memory()

        # Disks
        disk_root = psutil.disk_usage("/")
        disk_data = psutil.disk_usage("/data") if os.path.exists("/data") else None

        # Uptime
        uptime_sec = time.time() - psutil.boot_time()
        uptime_str = _format_uptime(uptime_sec)

        # Process checks
        ollama_active = False
        jarvis_active = False
        ollama_mem = 0
        jarvis_mem = 0

        for p in psutil.process_iter(["name", "cmdline", "memory_info"]):
            try:
                cmd = " ".join(p.info.get("cmdline") or []).lower()
                name = (p.info.get("name") or "").lower()
                if "ollama" in name or "llama-server" in cmd:
                    ollama_active = True
                    mem_info = p.info.get("memory_info")
                    if mem_info:
                        ollama_mem += mem_info.rss
                if "uvicorn" in cmd and "main:app" in cmd:
                    jarvis_active = True
                    mem_info = p.info.get("memory_info")
                    if mem_info:
                        jarvis_mem += mem_info.rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        lines = ["Current Server Status:"]
        lines.append(f"- CPU Usage: {cpu_percent}% across {cpu_count} vCPUs")
        lines.append(
            f"- Memory (RAM): {_format_bytes(mem.used)} / {_format_bytes(mem.total)} "
            f"({mem.percent}% used, {_format_bytes(mem.available)} free)"
        )
        lines.append(
            f"- Primary Storage (/): {_format_bytes(disk_root.free)} free of {_format_bytes(disk_root.total)} "
            f"({disk_root.percent}% used)"
        )
        if disk_data:
            lines.append(
                f"- Data Storage (/data): {_format_bytes(disk_data.free)} free of {_format_bytes(disk_data.total)} "
                f"({disk_data.percent}% used)"
            )
        lines.append(f"- System Uptime: {uptime_str}")

        service_parts = []
        if ollama_active:
            service_parts.append(f"Ollama SLM (Online, {_format_bytes(ollama_mem)} RAM)")
        else:
            service_parts.append("Ollama SLM (Offline)")

        if jarvis_active:
            service_parts.append(f"JARVIS Core (Online, {_format_bytes(jarvis_mem)} RAM)")
        else:
            service_parts.append("JARVIS Core (Active)")

        lines.append(f"- Core Services: {', '.join(service_parts)}")

        return "\n".join(lines)
    except Exception as exc:
        logger.error("Failed to gather system metrics: %s", exc)
        return f"System metric collection encountered an error: {exc}"


async def get_system_metrics(aspect: str = "all") -> str:
    """Async wrapper for gathering system metrics."""
    import asyncio
    return await asyncio.to_thread(get_system_status, aspect)
