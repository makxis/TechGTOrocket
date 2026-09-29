"""
ВРО-1 / RocketBoard — готовность к пуску и допустимые команды (без окна).

Два вопроса, которые оператор должен видеть сразу, без догадок:
  1. Можно ли пускать ракету? (readiness)
  2. Какие кнопки сейчас вообще имеют смысл? (allowed_commands)

Ответы строятся по последней телеметрии. Нет свежих данных означает
«не готов»: по неизвестному состоянию ракету не пускают.
"""

from typing import Optional, Set, Tuple

from telemetry import CRITICAL_MASK, describe_errors, STATE_RU

# Уровни: ok зелёный, warn жёлтый, bad красный, info синий/нейтральный.
OK, WARN, BAD, INFO = "ok", "warn", "bad", "info"

FLAG_BARO_CALIB = 0x0100
FLAG_LOW_BATTERY = 0x0200

IN_FLIGHT = ("BOOST", "COAST", "APOGEE", "DESCENT", "RECOVERY")

DATA_TIMEOUT_S = 1.5


def readiness(state: Optional[str], recovery: Optional[str], error_flags: int,
              data_age_s: Optional[float],
              timeout_s: float = DATA_TIMEOUT_S) -> Tuple[str, str]:
    """(уровень, крупный текст) по последним данным с борта."""
    if state is None or data_age_s is None or data_age_s > timeout_s:
        return BAD, "НЕТ ДАННЫХ С БОРТА: состояние неизвестно, пуск запрещён"

    if state in IN_FLIGHT:
        return INFO, "В ПОЛЁТЕ"

    if state == "LANDED":
        return BAD, ("ПОСАДКА: пуск невозможен. Уложите парашют и нажмите "
                     "«Вернуть в READY» (или перезагрузите плату питанием)")

    if state == "INIT":
        return WARN, "ИНИЦИАЛИЗАЦИЯ БОРТА: подождите"

    if state != "READY":
        return BAD, f"БОРТ В СОСТОЯНИИ «{STATE_RU.get(state, state)}»: пуск запрещён"

    # READY
    if recovery == "DEPLOYED":
        return BAD, ("ПАРАШЮТ УЖЕ РАСКРЫТ: пуск запрещён. Сложите парашют и нажмите "
                     "«Вернуть в READY»")
    if recovery == "ERROR":
        return BAD, "СИСТЕМА СПАСЕНИЯ В ОШИБКЕ: пуск запрещён"
    if recovery != "ARMED":
        return BAD, "СПАСЕНИЕ НЕ ВЗВЕДЕНО: пуск запрещён"

    if error_flags & CRITICAL_MASK:
        names = "; ".join(t for t in describe_errors(error_flags & CRITICAL_MASK))
        return BAD, f"КРИТИЧЕСКАЯ ОШИБКА: {names}. Пуск запрещён"

    warns = []
    if error_flags & FLAG_LOW_BATTERY:
        warns.append("батарея разряжена, сервопривод может не сработать")
    if error_flags & FLAG_BARO_CALIB:
        warns.append("нулевая высота не установлена, ракету держать неподвижно")
    if warns:
        return WARN, "ГОТОВ, НО: " + "; ".join(warns)

    return OK, "ГОТОВ К ПУСКУ"


def allowed_commands(state: Optional[str], recovery: Optional[str],
                     wired: bool = False) -> Set[str]:
    """
    Команды платы, которые в этом состоянии имеют смысл (символы кадров).
    Остальные кнопки выключаются, чтобы не нажимать то, что борт проигнорирует.
    Открыть парашют (D) сюда не входит: она живёт отдельно и в бою доступна всегда.
    """
    extra = set("i?") if wired else set()
    if state == "READY":
        cmds = set("sdtz0123456789")
        if recovery == "DEPLOYED":
            cmds.add("R")                # раскрыто вручную: только вернуть, профиль не запустится
        else:
            cmds.add("r")
        return cmds | extra
    if state == "LANDED":
        return {"R"} | extra
    return set() | extra


def short(text: str) -> str:
    """Короткая форма для строки состояния: до первой точки («ПОСАДКА: пуск невозможен»)."""
    return text.split(". ")[0].rstrip(".")
