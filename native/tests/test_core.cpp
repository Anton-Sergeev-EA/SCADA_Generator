// Модульные тесты C++ ядра без внешних зависимостей (запуск через ctest).
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <random>

#include "scada/holt_forecaster.hpp"
#include "scada/streaming_detector.hpp"

namespace {

int g_failures = 0;

void check(bool cond, const char* what) {
    if (!cond) {
        std::fprintf(stderr, "FAIL: %s\n", what);
        ++g_failures;
    } else {
        std::printf("ok   %s\n", what);
    }
}

void test_normal_signal_has_no_alarms() {
    scada::StreamingDetector det;
    std::mt19937 rng(42);
    std::normal_distribution<double> noise(0.0, 1.0);
    int anomalies = 0;
    for (int i = 0; i < 2000; ++i) {
        const auto r = det.update(50.0 + noise(rng));
        if (r.ready && r.score >= 0.5) {
            ++anomalies;
        }
    }
    check(anomalies < 10, "normal gaussian noise: false positive rate < 0.5%");
}

void test_spike_detected() {
    scada::StreamingDetector det;
    std::mt19937 rng(1);
    std::normal_distribution<double> noise(0.0, 1.0);
    for (int i = 0; i < 300; ++i) {
        det.update(20.0 + noise(rng));
    }
    const auto r = det.update(35.0);
    check((r.flags & scada::kSpike) != 0 && r.score >= 0.5, "15-sigma spike is flagged");
}

void test_drift_detected() {
    scada::StreamingDetector det;
    std::mt19937 rng(7);
    std::normal_distribution<double> noise(0.0, 0.5);
    for (int i = 0; i < 400; ++i) {
        det.update(80.0 + noise(rng));
    }
    bool drift = false;
    int direction = 0;
    for (int i = 0; i < 300 && !drift; ++i) {
        const auto r = det.update(80.0 + 0.02 * i + noise(rng));
        if ((r.flags & scada::kDrift) != 0) {
            drift = true;
            direction = r.drift_direction;
        }
    }
    check(drift && direction == 1, "slow upward drift is flagged with direction +1");
}

void test_stuck_detected() {
    scada::StreamingDetector det;
    std::mt19937 rng(3);
    std::normal_distribution<double> noise(0.0, 1.0);
    for (int i = 0; i < 300; ++i) {
        det.update(10.0 + noise(rng));
    }
    bool stuck = false;
    for (int i = 0; i < 30; ++i) {
        stuck = stuck || (det.update(10.7).flags & scada::kStuck) != 0;
    }
    check(stuck, "frozen sensor value is flagged as stuck");
}

void test_constant_signal_not_stuck() {
    scada::StreamingDetector det;
    bool any = false;
    for (int i = 0; i < 200; ++i) {
        any = any || det.update(1.0).score >= 0.5;
    }
    check(!any, "legitimately constant signal (e.g. coil) is not an anomaly");
}

void test_nan_is_ignored() {
    scada::StreamingDetector det;
    det.update(1.0);
    const auto r = det.update(std::nan(""));
    check(!r.ready && det.count() == 1, "NaN input is ignored");
}

void test_forecast_time_to_threshold() {
    scada::HoltForecaster f;
    std::mt19937 rng(5);
    std::normal_distribution<double> noise(0.0, 0.2);
    // 300 с стабильной работы (обучение нормы), затем рост 0.05 ед./с:
    // через 400 с уровень 50, до порога 100 остаётся ~1000 с.
    for (int t = 0; t < 300; ++t) {
        f.update(30.0 + noise(rng), static_cast<double>(t));
    }
    for (int t = 0; t < 400; ++t) {
        f.update(30.0 + 0.05 * t + noise(rng), static_cast<double>(300 + t));
    }
    const double eta = f.time_to(100.0);
    check(eta > 800.0 && eta < 1300.0, "ramp after stable period: time-to-threshold ~ 1000 s");
    check(f.time_to(0.0) < 0.0, "threshold behind the trend -> no forecast");
}

void test_forecast_flat_signal() {
    scada::HoltForecaster f;
    std::mt19937 rng(9);
    std::normal_distribution<double> noise(0.0, 1.0);
    int false_alarms = 0;
    for (int t = 0; t < 1000; ++t) {
        f.update(50.0 + noise(rng), static_cast<double>(t));
        if (t > 50 && f.time_to(60.0) > 0.0) {
            ++false_alarms;
        }
    }
    check(false_alarms < 50, "flat noisy signal: forecast rarely fires (<5%)");
}

void test_bank() {
    scada::DetectorBank bank(3, scada::DetectorConfig{});
    const auto res = bank.update({1.0, 2.0, 3.0});
    check(res.size() == 3 && bank.size() == 3, "bank updates all detectors at once");
}

}  // namespace

int main() {
    test_normal_signal_has_no_alarms();
    test_spike_detected();
    test_drift_detected();
    test_stuck_detected();
    test_constant_signal_not_stuck();
    test_nan_is_ignored();
    test_forecast_time_to_threshold();
    test_forecast_flat_signal();
    test_bank();
    if (g_failures != 0) {
        std::fprintf(stderr, "%d test(s) failed\n", g_failures);
        return EXIT_FAILURE;
    }
    std::printf("all native tests passed\n");
    return EXIT_SUCCESS;
}
