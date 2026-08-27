def parse_traffic_to_gb(traffic_str):
    if not traffic_str:
        return 0.0

    parts = traffic_str.strip().split()

    if not parts:
        return 0.0

    try:
        value = float(parts[0])
    except (ValueError, IndexError):
        return 0.0

    unit = parts[1].upper() if len(parts) > 1 else "GB"

    if unit.startswith("MB"):
        return value / 1024

    elif unit.startswith("KB"):
        return value / (1024 * 1024)

    return value

def bytes_to_mb(value):
    return value / (1024 * 1024)

def format_data_size(value_gb):
    if value_gb >= 1:
        return f"{value_gb:.2f} GB"

    return f"{round(value_gb * 1024)} MB"
