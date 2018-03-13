#!/usr/bin/env python3
#
# Copyright (c) 2016 Roberto Riggio
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied. See the License for the
# specific language governing permissions and limitations
# under the License.

"""NIF statistics module."""

from construct import UBInt8
from construct import UBInt16
from construct import UBInt32
from construct import UBInt64
from construct import Bytes
from construct import Sequence
from construct import Container
from construct import Struct
from construct import Array
from construct import BitStruct
from construct import Padding
from construct import Bit

from empower.core.resourcepool import BT_L20
from empower.core.app import EmpowerApp
from empower.datatypes.etheraddress import EtherAddress
from empower.lvapp.lvappserver import ModuleLVAPPWorker
from empower.core.module import ModulePeriodic
from empower.lvapp import PT_VERSION

from empower.main import RUNTIME


PT_NIF_REQUEST = 0x90
PT_NIF_RESPONSE = 0x91

NIF_STATS_ENTRY = Sequence("rates",
                       UBInt8("rate"),
                       BitStruct("flags",
                                 Padding(6),
                                 Bit("mcs"),
                                 Padding(9)),
                       UBInt32("prob"),
                       UBInt32("cur_prob"),
                       UBInt64("hist_successes"),
                       UBInt64("hist_attempts"),
                       UBInt32("last_successes"),
                       UBInt32("last_attempts"),
                       UBInt64("last_acked_bytes"),
                       UBInt64("hist_acked_bytes"))

NIF_STATS_REQUEST = Struct("nif_request", UBInt8("version"),
                       UBInt8("type"),
                       UBInt32("length"),
                       UBInt32("seq"),
                       UBInt32("module_id"),
                       Bytes("sta", 6))

NIF_STATS_RESPONSE = Struct("nif_response", UBInt8("version"),
                        UBInt8("type"),
                        UBInt32("length"),
                        UBInt32("seq"),
                        UBInt32("module_id"),
                        Bytes("wtp", 6),
                        UBInt16("nb_entries"),
                        Array(lambda ctx: ctx.nb_entries, NIF_STATS_ENTRY))


class NIFStats(ModulePeriodic):
    """ NIFStats object. """

    MODULE_NAME = "nif_stats"
    REQUIRED = ['module_type', 'worker', 'tenant_id', 'lvap']

    def __init__(self):

        super().__init__()

        # parameters
        self._lvap = None

        # data structures
        # This dictionary holds all the stats for each rate. 
        self.rates = {}
        self.best_prob = None

    def __eq__(self, other):

        return super().__eq__(other) and self.lvap == other.lvap

    @property
    def lvap(self):
        """Return LVAP Address."""

        return self._lvap

    @lvap.setter
    def lvap(self, value):
        """Set LVAP Address."""

        self._lvap = EtherAddress(value)

    def to_dict(self):
        """ Return a JSON-serializable."""

        out = super().to_dict()

        out['lvap'] = self.lvap
        out['best_prob'] = self.best_prob
        out['rates'] = {str(k): v for k, v in self.rates.items()}

        return out

    def run_once(self):
        """Send out nif stats request."""

        if self.tenant_id not in RUNTIME.tenants:
            self.log.info("Tenant %s not found", self.tenant_id)
            self.unload()
            return

        tenant = RUNTIME.tenants[self.tenant_id]

        if self.lvap not in tenant.lvaps:
            self.log.info("LVNF %s not found", self.lvap)
            self.unload()
            return

        lvap = tenant.lvaps[self.lvap]

        if not lvap.wtp.connection or lvap.wtp.connection.stream.closed():
            self.log.info("WTP %s not connected", lvap.wtp.addr)
            self.unload()
            return

        nif_req = Container(version=PT_VERSION,
                              type=PT_NIF_REQUEST,
                              length=20,
                              seq=lvap.wtp.seq,
                              module_id=self.module_id,
                              sta=lvap.addr.to_raw())

        self.log.info("Sending nif stats request to %s @ %s (id=%u)",
                      lvap.addr, lvap.wtp.addr, self.module_id)

        msg = NIF_STATS_REQUEST.build(nif_req)
        lvap.wtp.connection.stream.write(msg)

    def handle_response(self, response):
        """Handle an incoming NIF_STATS_RESPONSE message.
        Args:
            ???, a NIF_STATS_RESPONSE message
        Returns:
            None
        """
        tenant = RUNTIME.tenants[self.tenant_id]
        lvap = tenant.lvaps[self.lvap]

        # update this object
        self.rates = {}
        for entry in response.rates:
            if lvap.supported_band == BT_L20:
                rate = entry[0] / 2.0
            else:
                rate = entry[0]
            value = {'prob': entry[2] / 180.0,
                     'cur_prob': entry[3] / 180.0, 
                     'hist_successes': entry[4],
                     'hist_attempts': entry[5],
                     'last_successes': entry[6],
                     'last_attempts': entry[7],
                     'last_acked_bytes': entry[8],
                     'hist_acked_bytes': entry[9],}
            # this dictionary has all the values I need with key = rate and 
            # value = the sample for that rate 
            self.rates[rate] = value

        max_idx = max(self.rates.keys(),
                      key=(lambda key: self.rates[key]['prob']))
        max_val = self.rates[max_idx]['prob']

        self.best_prob = \
            max([k for k, v in self.rates.items() if v['prob'] == max_val])

        # call callback
        self.handle_callback(self)


class NIFStatsWorker(ModuleLVAPPWorker):
    """ Counter worker. """

    pass


def nif_stats(**kwargs):
    """Create a new module."""

    return RUNTIME.components[NIFStatsWorker.__module__].add_module(**kwargs)


def bound_nif_stats(self, **kwargs):
    """Create a new module (app version)."""

    kwargs['tenant_id'] = self.tenant.tenant_id
    return nif_stats(**kwargs)

setattr(EmpowerApp, NIFStats.MODULE_NAME, bound_nif_stats)


def launch():
    """ Initialize the module. """

    return NIFStatsWorker(NIFStats, PT_NIF_RESPONSE, NIF_STATS_RESPONSE)
