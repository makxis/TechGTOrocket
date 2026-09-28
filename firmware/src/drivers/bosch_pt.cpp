/*
 * ВРО-1 / RocketBoard — компенсация давления и температуры Bosch
 *
 * Формулы взяты из документации Bosch на BMP280 без изменений, включая
 * порядок сдвигов. Переписывать их «покрасивее» не стоит: целочисленная
 * арифметика здесь подобрана так, чтобы не терять разряды, и любая
 * перестановка операций меняет результат.
 */

#include "bosch_pt.h"

namespace BoschPT {

bool parseCalib(const uint8_t *b, Calib &c)
{
    c.T1 = (uint16_t)(b[0]  | (b[1]  << 8));
    c.T2 = (int16_t) (b[2]  | (b[3]  << 8));
    c.T3 = (int16_t) (b[4]  | (b[5]  << 8));
    c.P1 = (uint16_t)(b[6]  | (b[7]  << 8));
    c.P2 = (int16_t) (b[8]  | (b[9]  << 8));
    c.P3 = (int16_t) (b[10] | (b[11] << 8));
    c.P4 = (int16_t) (b[12] | (b[13] << 8));
    c.P5 = (int16_t) (b[14] | (b[15] << 8));
    c.P6 = (int16_t) (b[16] | (b[17] << 8));
    c.P7 = (int16_t) (b[18] | (b[19] << 8));
    c.P8 = (int16_t) (b[20] | (b[21] << 8));
    c.P9 = (int16_t) (b[22] | (b[23] << 8));

    c.t_fine = 0;

    /* T1 и P1 нулевыми быть не могут ни у одного исправного экземпляра.
     * Если они нулевые, значит обмен не состоялся и компенсация выдаст
     * бессмыслицу — лучше сразу признать датчик неисправным. */
    return (c.T1 != 0) && (c.P1 != 0);
}

int32_t compensateT(Calib &c, int32_t adc_T)
{
    int32_t var1, var2;

    var1 = ((((adc_T >> 3) - ((int32_t)c.T1 << 1))) * ((int32_t)c.T2)) >> 11;
    var2 = (((((adc_T >> 4) - ((int32_t)c.T1)) *
              ((adc_T >> 4) - ((int32_t)c.T1))) >> 12) * ((int32_t)c.T3)) >> 14;

    c.t_fine = var1 + var2;
    return (c.t_fine * 5 + 128) >> 8;
}

uint32_t compensateP(const Calib &c, int32_t adc_P)
{
    int32_t var1, var2;
    uint32_t p;

    var1 = (((int32_t)c.t_fine) >> 1) - (int32_t)64000;
    var2 = (((var1 >> 2) * (var1 >> 2)) >> 11) * ((int32_t)c.P6);
    var2 = var2 + ((var1 * ((int32_t)c.P5)) << 1);
    var2 = (var2 >> 2) + (((int32_t)c.P4) << 16);
    var1 = (((c.P3 * (((var1 >> 2) * (var1 >> 2)) >> 13)) >> 3) +
            ((((int32_t)c.P2) * var1) >> 1)) >> 18;
    var1 = ((((32768 + var1)) * ((int32_t)c.P1)) >> 15);

    if (var1 == 0)
        return 0;               /* деление на ноль, отдаём заведомо брак */

    p = (((uint32_t)(((int32_t)1048576) - adc_P) - (var2 >> 12))) * 3125;

    if (p < 0x80000000UL)
        p = (p << 1) / ((uint32_t)var1);
    else
        p = (p / (uint32_t)var1) * 2;

    var1 = (((int32_t)c.P9) * ((int32_t)(((p >> 3) * (p >> 3)) >> 13))) >> 12;
    var2 = (((int32_t)(p >> 2)) * ((int32_t)c.P8)) >> 13;
    p = (uint32_t)((int32_t)p + ((var1 + var2 + c.P7) >> 4));

    return p;
}

} /* namespace BoschPT */
