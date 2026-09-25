// Потоковый детектор аномалий для одного технологического тега.
//
// Работает за O(1) по времени и памяти на отсчёт, не хранит историю.
// Сочетает три независимых механизма:
//   * spike  — выброс: |z| быстрой робастной EWMA-модели выше порога;
//   * drift  — устойчивый уход сглаженного сигнала от долгосрочной
//              базовой линии (в единицах её σ, с гистерезисом). В отличие
//              от классического CUSUM не даёт ложных срабатываний на
//              автокоррелированных сигналах (циклический отбор и т.п.);
//   * stuck  — залипание датчика: значение перестало меняться, хотя
//              в норме сигнал шумит.
// Алгоритм зеркально реализован в scada_core/ml/fallback.py — результаты
// должны совпадать (проверяется тестами tests/test_native_parity.py).
#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

namespace scada {

struct DetectorConfig {
    double fast_alpha = 0.1;    // скорость адаптации быстрой модели
    double slow_alpha = 0.005;  // скорость адаптации базовой линии
    int warmup = 30;            // отсчётов до начала выдачи решений
    double z_threshold = 4.0;   // порог выброса в сигмах
    double mid_alpha = 0.05;  // сглаживание сигнала для оценки дрейфа
    double drift_threshold = 3.0;  // уход от базовой линии, σ
    int drift_warmup = 200;  // отсчётов на обучение базовой линии
    int stuck_window = 15;   // отсчётов без изменений = залипание
    double min_std = 1e-3;   // нижняя граница σ (квантование АЦП)
    double huber_c = 3.0;    // обрезка остатка при обучении (σ)
};

enum Flag : std::uint8_t {
    kNone = 0,
    kSpike = 1U << 0U,
    kDrift = 1U << 1U,
    kStuck = 1U << 2U,
};

struct DetectorResult {
    double score = 0.0;      // 0..1, >= 0.5 означает аномалию
    double z = 0.0;          // отклонение от быстрой модели, σ
    double expected = 0.0;   // ожидаемое значение (быстрая модель)
    double sigma = 0.0;      // оценка шума
    double baseline = 0.0;   // базовая линия (медленная модель)
    double band_low = 0.0;   // нижняя граница «коридора нормы»
    double band_high = 0.0;  // верхняя граница «коридора нормы»
    double drift = 0.0;  // |сглаженный - базовая| / σ базовой линии
    int drift_direction = 0;  // +1 вверх, -1 вниз, 0 нет
    std::uint8_t flags = kNone;
    bool ready = false;  // прогрев завершён
};

class StreamingDetector {
public:
    StreamingDetector() = default;
    explicit StreamingDetector(const DetectorConfig& config);

    DetectorResult update(double x);

    // Принять текущее состояние процесса как новую норму
    // (например, после штатной смены уставки оператором).
    void rebase();
    void reset();

    [[nodiscard]] std::int64_t count() const noexcept { return n_; }
    [[nodiscard]] const DetectorConfig& config() const noexcept { return cfg_; }

private:
    [[nodiscard]] double floor_std(double level) const noexcept;

    DetectorConfig cfg_{};
    std::int64_t n_ = 0;
    double fast_mean_ = 0.0;
    double fast_var_ = 0.0;
    double base_mean_ = 0.0;
    double base_var_ = 0.0;
    double mid_mean_ = 0.0;
    bool drifting_ = false;
    double last_x_ = 0.0;
    int same_count_ = 0;
};

// Набор детекторов для всех тегов установки: один вызов на цикл опроса.
class DetectorBank {
public:
    DetectorBank(std::size_t size, const DetectorConfig& config);

    std::vector<DetectorResult> update(const std::vector<double>& values);
    void rebase_all();
    void rebase(std::size_t index);

    [[nodiscard]] std::size_t size() const noexcept { return detectors_.size(); }

private:
    std::vector<StreamingDetector> detectors_;
};

}  // namespace scada
