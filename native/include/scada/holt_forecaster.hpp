// Прогноз «время до аларма» (predictive alarm).
//
// Двойное экспоненциальное сглаживание Холта с учётом неравномерного
// шага по времени: оценивает уровень и скорость изменения тега и
// отвечает на вопрос «через сколько секунд значение пересечёт порог».
// Прогноз выдаётся, только если текущий тренд необычен для этого тега:
// он сравнивается с долговременной статистикой тренда (RMS), выученной
// на нормальной работе. Поэтому штатные колебания процесса (цикличный
// отбор, работа регулятора) не порождают ложных предупреждений.
#pragma once

#include <cstdint>

namespace scada {

struct ForecastConfig {
    double alpha = 0.2;         // сглаживание уровня
    double beta = 0.01;         // сглаживание тренда (~100 с)
    double noise_alpha = 0.05;  // сглаживание дисперсии остатков
    double long_alpha = 0.002;  // обучение «нормального» разброса тренда
    int min_samples = 120;  // минимум отсчётов для прогноза
    double horizon_s = 1800.0;  // дальше этого горизонта не прогнозируем
    double significance = 3.0;  // во сколько раз |тренд| выше нормального RMS
};

class HoltForecaster {
public:
    HoltForecaster() = default;
    explicit HoltForecaster(const ForecastConfig& config);

    void update(double x, double t_seconds);

    // Секунды до пересечения порога; отрицательное значение —
    // пересечения в пределах горизонта не ожидается.
    [[nodiscard]] double time_to(double threshold) const noexcept;

    // Значение через dt секунд.
    [[nodiscard]] double predict(double dt_seconds) const noexcept;

    void reset();

    [[nodiscard]] double level() const noexcept { return level_; }
    [[nodiscard]] double trend() const noexcept { return trend_; }
    [[nodiscard]] double noise() const noexcept;
    [[nodiscard]] double trend_scale() const noexcept;  // нормальный RMS тренда
    [[nodiscard]] bool trend_significant() const noexcept;
    [[nodiscard]] std::int64_t count() const noexcept { return n_; }

private:
    ForecastConfig cfg_{};
    std::int64_t n_ = 0;
    double level_ = 0.0;
    double trend_ = 0.0;  // единиц в секунду
    double resid_var_ = 0.0;
    double trend_ms_ = 0.0;  // средний квадрат тренда в нормальной работе
    double last_t_ = 0.0;
};

}  // namespace scada
