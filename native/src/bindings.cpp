// Python-привязка C++ ядра: модуль scada_core.ml._native
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "scada/holt_forecaster.hpp"
#include "scada/streaming_detector.hpp"

namespace py = pybind11;
using scada::DetectorBank;
using scada::DetectorConfig;
using scada::DetectorResult;
using scada::ForecastConfig;
using scada::HoltForecaster;
using scada::StreamingDetector;

PYBIND11_MODULE(_native, m) {
    m.doc() = "SCADA Generator native analytics core (C++17)";
    m.attr("FLAG_SPIKE") = static_cast<int>(scada::kSpike);
    m.attr("FLAG_DRIFT") = static_cast<int>(scada::kDrift);
    m.attr("FLAG_STUCK") = static_cast<int>(scada::kStuck);

    py::class_<DetectorConfig>(m, "DetectorConfig")
        .def(py::init<>())
        .def_readwrite("fast_alpha", &DetectorConfig::fast_alpha)
        .def_readwrite("slow_alpha", &DetectorConfig::slow_alpha)
        .def_readwrite("warmup", &DetectorConfig::warmup)
        .def_readwrite("z_threshold", &DetectorConfig::z_threshold)
        .def_readwrite("mid_alpha", &DetectorConfig::mid_alpha)
        .def_readwrite("drift_threshold", &DetectorConfig::drift_threshold)
        .def_readwrite("drift_warmup", &DetectorConfig::drift_warmup)
        .def_readwrite("stuck_window", &DetectorConfig::stuck_window)
        .def_readwrite("min_std", &DetectorConfig::min_std)
        .def_readwrite("huber_c", &DetectorConfig::huber_c);

    py::class_<DetectorResult>(m, "DetectorResult")
        .def_readonly("score", &DetectorResult::score)
        .def_readonly("z", &DetectorResult::z)
        .def_readonly("expected", &DetectorResult::expected)
        .def_readonly("sigma", &DetectorResult::sigma)
        .def_readonly("baseline", &DetectorResult::baseline)
        .def_readonly("band_low", &DetectorResult::band_low)
        .def_readonly("band_high", &DetectorResult::band_high)
        .def_readonly("drift", &DetectorResult::drift)
        .def_readonly("drift_direction", &DetectorResult::drift_direction)
        .def_readonly("flags", &DetectorResult::flags)
        .def_readonly("ready", &DetectorResult::ready);

    py::class_<StreamingDetector>(m, "StreamingDetector")
        .def(py::init<>())
        .def(py::init<const DetectorConfig&>(), py::arg("config"))
        .def("update", &StreamingDetector::update, py::arg("x"))
        .def("rebase", &StreamingDetector::rebase)
        .def("reset", &StreamingDetector::reset)
        .def_property_readonly("count", &StreamingDetector::count);

    py::class_<DetectorBank>(m, "DetectorBank")
        .def(py::init<std::size_t, const DetectorConfig&>(), py::arg("size"), py::arg("config"))
        .def("update", &DetectorBank::update, py::arg("values"),
             py::call_guard<py::gil_scoped_release>())
        .def("rebase_all", &DetectorBank::rebase_all)
        .def("rebase", &DetectorBank::rebase, py::arg("index"))
        .def("__len__", &DetectorBank::size);

    py::class_<ForecastConfig>(m, "ForecastConfig")
        .def(py::init<>())
        .def_readwrite("alpha", &ForecastConfig::alpha)
        .def_readwrite("beta", &ForecastConfig::beta)
        .def_readwrite("noise_alpha", &ForecastConfig::noise_alpha)
        .def_readwrite("long_alpha", &ForecastConfig::long_alpha)
        .def_readwrite("min_samples", &ForecastConfig::min_samples)
        .def_readwrite("horizon_s", &ForecastConfig::horizon_s)
        .def_readwrite("significance", &ForecastConfig::significance);

    py::class_<HoltForecaster>(m, "HoltForecaster")
        .def(py::init<>())
        .def(py::init<const ForecastConfig&>(), py::arg("config"))
        .def("update", &HoltForecaster::update, py::arg("x"), py::arg("t_seconds"))
        .def("time_to", &HoltForecaster::time_to, py::arg("threshold"))
        .def("predict", &HoltForecaster::predict, py::arg("dt_seconds"))
        .def("reset", &HoltForecaster::reset)
        .def_property_readonly("level", &HoltForecaster::level)
        .def_property_readonly("trend", &HoltForecaster::trend)
        .def_property_readonly("noise", &HoltForecaster::noise)
        .def_property_readonly("trend_scale", &HoltForecaster::trend_scale)
        .def_property_readonly("trend_significant", &HoltForecaster::trend_significant)
        .def_property_readonly("count", &HoltForecaster::count);
}
