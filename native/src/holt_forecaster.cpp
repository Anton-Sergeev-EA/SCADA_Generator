#include "scada/holt_forecaster.hpp"

#include <algorithm>
#include <cmath>

namespace scada {

HoltForecaster::HoltForecaster(const ForecastConfig& config) : cfg_(config) {}

void HoltForecaster::reset() {
    n_ = 0;
    level_ = trend_ = resid_var_ = trend_ms_ = last_t_ = 0.0;
}

double HoltForecaster::noise() const noexcept { return std::sqrt(resid_var_); }

double HoltForecaster::trend_scale() const noexcept {
    // Нижняя граница: тренд, сдвигающий значение на одну σ шума за горизонт.
    return std::max(std::sqrt(trend_ms_), noise() / cfg_.horizon_s);
}

bool HoltForecaster::trend_significant() const noexcept {
    return n_ >= cfg_.min_samples && std::fabs(trend_) > cfg_.significance * trend_scale();
}

void HoltForecaster::update(double x, double t_seconds) {
    if (!std::isfinite(x) || !std::isfinite(t_seconds)) {
        return;
    }
    if (n_ == 0) {
        level_ = x;
        trend_ = 0.0;
        resid_var_ = 0.0;
        last_t_ = t_seconds;
        n_ = 1;
        return;
    }
    const double dt = t_seconds - last_t_;
    if (dt <= 0.0) {
        return;
    }
    ++n_;
    const double pred = level_ + trend_ * dt;
    const double err = x - pred;
    const double new_level = cfg_.alpha * x + (1.0 - cfg_.alpha) * pred;
    const double slope = (new_level - level_) / dt;
    const double inv_n = 1.0 / static_cast<double>(n_);
    const double g = std::max(cfg_.noise_alpha, inv_n);
    trend_ = cfg_.beta * slope + (1.0 - cfg_.beta) * trend_;
    level_ = new_level;
    resid_var_ = (1.0 - g) * (resid_var_ + g * err * err);
    // Необычный тренд не попадает в статистику «нормы».
    if (!trend_significant()) {
        const double gl = std::max(cfg_.long_alpha, inv_n);
        trend_ms_ = (1.0 - gl) * trend_ms_ + gl * trend_ * trend_;
    }
    last_t_ = t_seconds;
}

double HoltForecaster::predict(double dt_seconds) const noexcept {
    return level_ + trend_ * dt_seconds;
}

double HoltForecaster::time_to(double threshold) const noexcept {
    if (!trend_significant() || !std::isfinite(threshold)) {
        return -1.0;
    }
    const double eta = (threshold - level_) / trend_;
    if (eta <= 0.0 || eta > cfg_.horizon_s) {
        return -1.0;
    }
    return eta;
}

}  // namespace scada
