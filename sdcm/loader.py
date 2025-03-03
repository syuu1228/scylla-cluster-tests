# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
#
# See LICENSE for more details.
#
# Copyright (c) 2016 ScyllaDB

import os
import re
from abc import abstractmethod, ABCMeta
import time
import logging
from typing import NamedTuple

from sdcm.prometheus import NemesisMetrics
from sdcm.utils.common import FileFollowerThread, convert_metric_to_ms

LOGGER = logging.getLogger(__name__)


class MetricsPosition(NamedTuple):
    ops: int
    lat_mean: int
    lat_med: int
    lat_perc_95: int
    lat_perc_99: int
    lat_perc_999: int
    lat_max: int
    errors: int


# pylint: disable=too-many-instance-attributes
class StressExporter(FileFollowerThread, metaclass=ABCMeta):
    METRICS_GAUGES = {}

    # pylint: disable=too-many-arguments
    def __init__(self, instance_name: str, metrics: NemesisMetrics, stress_operation: str, stress_log_filename: str,
                 loader_idx: int, cpu_idx: int = 1):
        super().__init__()
        self.metrics = metrics
        self.stress_operation = stress_operation
        self.stress_log_filename = stress_log_filename
        gauge_name = self.create_metrix_gauge()
        self.stress_metric = self.METRICS_GAUGES[gauge_name]
        self.instance_name = instance_name
        self.loader_idx = loader_idx
        self.cpu_idx = cpu_idx
        self.metrics_positions = self.merics_position_in_log()
        self.keyspace = ''

    @abstractmethod
    def merics_position_in_log(self) -> MetricsPosition:
        ...

    @abstractmethod
    def create_metrix_gauge(self) -> str:
        ...

    def set_metric(self, name: str, value: float) -> None:
        self.stress_metric.labels(0, self.instance_name, self.loader_idx, self.cpu_idx, name, self.keyspace).set(value)

    def clear_metrics(self) -> None:
        if self.stress_metric:
            for metric_name in self.metrics_positions._fields:
                self.set_metric(metric_name, 0.0)

    @staticmethod
    @abstractmethod
    def skip_line(line: str) -> bool:
        ...

    @staticmethod
    @abstractmethod
    def split_line(line: str) -> list:
        ...

    def get_metric_value(self, columns: list, metric_name: str) -> str:
        try:
            value = columns[getattr(self.metrics_positions, metric_name)]
        except (ValueError, IndexError) as exc:
            value = ''
            LOGGER.warning("Faile to get %s metric value. Error: %s", metric_name, str(exc))

        return value

    def run(self):
        while not self.stopped():
            exists = os.path.isfile(self.stress_log_filename)
            if not exists:
                time.sleep(0.5)
                continue

            for line in self.follow_file(self.stress_log_filename):
                if self.stopped():
                    break

                if self.skip_line(line=line):
                    continue

                cols = self.split_line(line=line)

                for metric in ['lat_mean', 'lat_med', 'lat_perc_95', 'lat_perc_99', 'lat_perc_999', 'lat_max']:
                    if metric_value := self.get_metric_value(columns=cols, metric_name=metric):
                        self.set_metric(metric, convert_metric_to_ms(metric_value))

                if ops := self.get_metric_value(columns=cols, metric_name='ops'):
                    self.set_metric('ops', float(ops))

                if errors := cols[self.metrics_positions.errors]:
                    self.set_metric('errors', int(errors))


class CassandraStressExporter(StressExporter):
    # pylint: disable=too-many-arguments
    def __init__(self, instance_name: str, metrics: NemesisMetrics, stress_operation: str, stress_log_filename: str,
                 loader_idx: int, cpu_idx: int = 1):

        self.keyspace_regex = re.compile(r'.*Keyspace:\s(.*?)$')
        super().__init__(instance_name, metrics, stress_operation, stress_log_filename, loader_idx,
                         cpu_idx)

    def create_metrix_gauge(self):
        gauge_name = f'collectd_cassandra_stress_{self.stress_operation}_gauge'
        if gauge_name not in self.METRICS_GAUGES:
            self.METRICS_GAUGES[gauge_name] = self.metrics.create_gauge(
                gauge_name,
                'Gauge for cassandra stress metrics',
                [f'cassandra_stress_{self.stress_operation}', 'instance', 'loader_idx', 'cpu_idx', 'type', 'keyspace'])
        return gauge_name

    def merics_position_in_log(self) -> MetricsPosition:
        return MetricsPosition(ops=2, lat_mean=5, lat_med=6, lat_perc_95=7, lat_perc_99=8, lat_perc_999=9,
                               lat_max=10, errors=13)

    def skip_line(self, line: str) -> bool:
        if not self.keyspace:
            if 'Keyspace:' in line:
                self.keyspace = self.keyspace_regex.match(line).groups()[0]
        # If line starts with 'total,' - skip this line
        return not 'total,' in line

    @staticmethod
    def split_line(line: str) -> list:
        return [element.strip() for element in line.split(',')]


class ScyllaBenchStressExporter(StressExporter):

    def create_metrix_gauge(self) -> str:
        gauge_name = f'collectd_scylla_bench_stress_{self.stress_operation}_gauge'
        if gauge_name not in self.METRICS_GAUGES:
            self.METRICS_GAUGES[gauge_name] = self.metrics.create_gauge(
                gauge_name,
                'Gauge for scylla-bench stress metrics',
                [f'scylla_bench_stress_{self.stress_operation}', 'instance', 'loader_idx', 'cpu_idx', 'type', 'keyspace'])
        return gauge_name

    # pylint: disable=line-too-long
    def merics_position_in_log(self) -> MetricsPosition:
        # Enumerate stress metric position in the log. Example:
        # time  operations/s    rows/s   errors  max   99.9th   99th      95th     90th       median        mean
        # 1.033603151s    3439    34390    0  71.434239ms   70.713343ms   62.685183ms    2.818047ms  1.867775ms 1.048575ms  2.947276ms

        return MetricsPosition(ops=1, lat_mean=10, lat_med=9, lat_perc_95=7, lat_perc_99=6, lat_perc_999=5,
                               lat_max=4, errors=3)

    # pylint: disable=line-too-long
    def skip_line(self, line) -> bool:
        # If line is not starts with numeric ended by "s" - skip this line.
        # Example:
        #    Client compression:  true
        #    1.004777157s       2891    28910     0  67.829759ms   64.290815ms    58.327039ms    4.653055ms   3.244031µs   1.376255µs

        line_splitted = (line or '').split()
        if not line_splitted or not line_splitted[0].endswith('s'):
            return True  # skip the line

        try:
            _ = float(line_splitted[0][:-1])
            return False  # The line hold the metrics, don't skip the line
        except ValueError:
            return True  # skip the line

    @staticmethod
    def split_line(line: str) -> list:
        return [element.strip() for element in line.split()]


class CassandraHarryStressExporter(StressExporter):

    # pylint: disable=too-many-arguments,useless-super-delegation
    def __init__(self, instance_name: str, metrics: NemesisMetrics, stress_operation: str, stress_log_filename: str,
                 loader_idx: int, cpu_idx: int = 1):

        super().__init__(instance_name, metrics, stress_operation, stress_log_filename,
                         loader_idx, cpu_idx)

    def create_metrix_gauge(self) -> str:
        gauge_name = f'collectd_cassandra_harry_stress_{self.stress_operation}_gauge'
        if gauge_name not in self.METRICS_GAUGES:
            self.METRICS_GAUGES[gauge_name] = self.metrics.create_gauge(
                gauge_name,
                'Gauge for scylla-bench stress metrics',
                [f'scylla_bench_stress_{self.stress_operation}', 'instance', 'loader_idx', 'cpu_idx', 'type', 'keyspace'])
        return gauge_name

    def merics_position_in_log(self) -> MetricsPosition:
        pass

    def skip_line(self, line) -> bool:
        return not 'Reorder buffer size has grown up to' in line

    @staticmethod
    def split_line(line: str) -> list:
        return line.split()
