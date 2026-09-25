#include "scada/streaming_detector.hpp"

#include <algorithm>
#include <cmath>

namespace scada {

namespace {

constexpr double kBandSigma = 3.0;
constexpr double kBaselineGuardSigma = 3.0;
// После обучения дисперсия базовой линии адаптируется в 5 раз медленнее
// среднего: иначе медленный дрейф «раздувает» σ и маскирует сам себя.
constexpr double kVarSlowdown = 0.2;

double clamp_abs(double v, double limit) noexcept { return std::clamp(v, -limit, limit); }

}  // namespace

StreamingDetector::StreamingDetector(const DetectorConfig& config) : cfg_(config) {}

double StreamingDetector::floor_std(double level) const noexcept {
    return std::max(cfg_.min_std, 1e-4 * std::fabs(level));
}

void StreamingDetector::reset() {
    n_ = 0;
    fast_mean_ = fast_var_ = base_mean_ = base_var_ = 0.0;
    mid_mean_ = 0.0;
    drifting_ = false;
    last_x_ = 0.0;
    same_count_ = 0;
}

void StreamingDetector::rebase() {
    base_mean_ = mid_mean_;
    base_var_ = std::max(base_var_, fast_var_);
    drifting_ = false;
}

DetectorResult StreamingDetector::update(double x) {
    DetectorResult res;
    if (!std::isfinite(x)) {
        res.expected = fast_mean_;
        res.baseline = base_mean_;
        return res;
    }

    ++n_;
    if (n_ == 1) {
        fast_mean_ = base_mean_ = mid_mean_ = x;
        fast_var_ = base_var_ = 0.0;
        last_x_ = x;
        same_count_ = 0;
        const double s = floor_std(x);
        res.expected = res.baseline = x;
        res.sigma = s;
        res.band_low = x - kBandSigma * s;
        res.band_high = x + kBandSigma * s;
        return res;
    }

    const bool ready = n_ > cfg_.warmup;
    const double sigma = std::max(std::sqrt(fast_var_), floor_std(fast_mean_));
    const double bsigma = std::max(std::sqrt(base_var_), floor_std(base_mean_));
    const double r = x - fast_mean_;
    const double z = r / sigma;
    const double inv_n = 1.0 / static_cast<double>(n_);
    const double am = std::max(cfg_.mid_alpha, inv_n);
    mid_mean_ += am * (x - mid_mean_);
    const double drift = std::fabs(mid_mean_ - base_mean_) / bsigma;
    const bool drift_ready = n_ > std::max<std::int64_t>(cfg_.warmup, cfg_.drift_warmup);

    const double eps = 1e-9 + 1e-9 * std::fabs(x);
    same_count_ = (std::fabs(x - last_x_) <= eps) ? same_count_ + 1 : 0;
    last_x_ = x;

    std::uint8_t flags = kNone;
    int direction = 0;
    if (ready) {
        if (std::fabs(z) > cfg_.z_threshold) {
            flags |= kSpike;
        }
        if (drift_ready) {
            // Гистерезис: включается на пороге, выключается на 2/3 порога.
            const double off = cfg_.drift_threshold * (2.0 / 3.0);
            drifting_ = drifting_ ? drift > off : drift > cfg_.drift_threshold;
        }
        if (drifting_) {
            flags |= kDrift;
            direction = mid_mean_ >= base_mean_ ? 1 : -1;
        }
        if (same_count_ >= cfg_.stuck_window &&
            std::sqrt(base_var_) > 3.0 * floor_std(base_mean_)) {
            flags |= kStuck;
        }
    }

    // Обучение быстрой модели (с обрезкой Хьюбера после прогрева).
    const double af = std::max(cfg_.fast_alpha, inv_n);
    const double rc = ready ? clamp_abs(r, cfg_.huber_c * sigma) : r;
    fast_mean_ += af * rc;
    fast_var_ = (1.0 - af) * (fast_var_ + af * rc * rc);

    // Базовая линия не учится на аномалиях — иначе дрейф «растворится».
    // Пока базовая линия учится (drift_warmup), берётся полная статистика
    // (alpha = 1/n); затем — медленное обновление с обрезкой выбросов.
    // Учимся, только пока сглаженный сигнал близок к базе (< половины
    // порога дрейфа): начавшийся дрейф не должен «переучивать» норму.
    const bool learn_base =
        !drift_ready || (drift < 0.5 * cfg_.drift_threshold && (flags & kStuck) == 0);
    if (learn_base) {
        const double as = std::max(cfg_.slow_alpha, inv_n);
        double rb = x - base_mean_;
        if (drift_ready) {
            rb = clamp_abs(rb, kBaselineGuardSigma * bsigma);
        }
        const double av = drift_ready ? as * kVarSlowdown : as;
        base_mean_ += as * rb;
        base_var_ = (1.0 - av) * (base_var_ + av * rb * rb);
    }

    double raw = 0.0;
    if (ready) {
        double drift_part = drift_ready ? drift / cfg_.drift_threshold : 0.0;
        if (drifting_) {
            drift_part = std::max(drift_part, 1.0);
        }
        raw = std::max(
            {std::fabs(z) / cfg_.z_threshold, drift_part, (flags & kStuck) != 0 ? 1.0 : 0.0});
    }

    res.score = raw / (1.0 + raw);
    res.z = z;
    res.expected = x - r;  // прогноз модели до учёта текущего отсчёта
    res.sigma = sigma;
    res.baseline = base_mean_;
    res.band_low = res.expected - kBandSigma * sigma;
    res.band_high = res.expected + kBandSigma * sigma;
    res.drift = drift;
    res.drift_direction = direction;
    res.flags = flags;
    res.ready = ready;
    return res;
}

DetectorBank::DetectorBank(std::size_t size, const DetectorConfig& config)
    : detectors_(size, StreamingDetector(config)) {}

std::vector<DetectorResult> DetectorBank::update(const std::vector<double>& values) {
    std::vector<DetectorResult> out;
    const std::size_t n = std::min(values.size(), detectors_.size());
    out.reserve(n);
    for (std::size_t i = 0; i < n; ++i) {
        out.push_back(detectors_[i].update(values[i]));
    }
    return out;
}

void DetectorBank::rebase_all() {
    for (auto& d : detectors_) {
        d.rebase();
    }
}

void DetectorBank::rebase(std::size_t index) {
    if (index < detectors_.size()) {
        detectors_[index].rebase();
    }
}

}  // namespace scada
