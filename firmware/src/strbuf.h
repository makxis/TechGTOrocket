// SPDX-License-Identifier: MIT
// Copyright (c) 2026 makxis
/*
 * ВРО-1 / RocketBoard — сборка строк в статическом буфере
 *
 * Зачем свой велосипед вместо snprintf_P: реализация printf в avr-libc
 * тянет за собой разбор форматной строки и весит около полутора
 * килобайт флеша. При 28 672 байтах на всю программу это непозволительно
 * дорого ради пары строк телеметрии — с ним набор D в плату не помещался.
 *
 * Заодно снимается риск, запрещённый п. 30 ТЗ: здесь нет ни динамического
 * выделения памяти, ни класса String, а выход за границу буфера
 * невозможен по устройству — каждая функция получает конец буфера.
 */

#ifndef STRBUF_H
#define STRBUF_H

#include <Arduino.h>

namespace StrBuf {

/* Все функции возвращают новую позицию записи. Если места не осталось,
 * данные просто отбрасываются, а позиция не выходит за end - 1:
 * место под завершающий ноль сохраняется всегда. */

char *addChar(char *p, char *end, char c);
char *addStr(char *p, char *end, const char *s);

/* Строка из флеша, например имя состояния из types.cpp. */
char *addProgmem(char *p, char *end, const __FlashStringHelper *s);

char *addULong(char *p, char *end, uint32_t v);
char *addLong(char *p, char *end, int32_t v);

/* Число с фиксированным количеством знаков после запятой. */
char *addFloat(char *p, char *end, float v, uint8_t prec);

/* Беззнаковое число с ведущими нулями до заданной ширины.
 * Нужно для имён файлов вида FLT0007.CSV (п. 18 ТЗ). */
char *addPadded(char *p, char *end, uint16_t v, uint8_t width);

inline void terminate(char *p)
{
    *p = '\0';
}

} /* namespace StrBuf */

#endif /* STRBUF_H */
