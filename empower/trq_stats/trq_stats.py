#!/usr/bin/env python3
#
# Copyright (c) 2017 Roberto Riggio
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

"""Traffic rules statistics module."""

from construct import UBInt8
from construct import UBInt16
from construct import UBInt32
from construct import Bytes
from construct import Sequence
from construct import Container
from construct import Struct
from construct import Array
from construct import BitStruct
from construct import Padding
from construct import Bit

from empower.core.app import EmpowerApp
from empower.datatypes.etheraddress import EtherAddress
from empower.lvapp.lvappserver import ModuleLVAPPWorker
from empower.core.module import ModulePeriodic
from empower.core.resourcepool import ResourceBlock
from empower.lvapp import PT_VERSION

from empower.main import RUNTIME


PT_TRQ_STATS_REQUEST = 0x59
PT_TRQ_STATS_RESPONSE = 0x60


TRQ_STATS_REQUEST = Struct("trq_stats_request", UBInt8("version"),
                           UBInt8("type"),
                           UBInt32("length"),
                           UBInt32("seq"),
                           UBInt32("module_id"),
                           Bytes("hwaddr", 6),
                           UBInt8("channel"),
                           UBInt8("band"),
                           UBInt8("dscp"),
                           Bytes("ssid", lambda ctx: ctx.length - 23))

TRQ_STATS_RESPONSE = Struct("trq_stats_response", UBInt8("version"),
                            UBInt8("type"),
                            UBInt32("length"),
                            UBInt32("seq"),
                            UBInt32("module_id"),
                            Bytes("wtp", 6),
                            BitStruct("flags", Padding(15),
                                      Bit("amsdu_aggregation")),
                            UBInt32("deficit_used"),
                            UBInt32("tx_pkts"),
                            UBInt32("tx_bytes"),
                            UBInt32("max_queue_length"))


class TRQStats(ModulePeriodic):
    """ TRStats object. """

    MODULE_NAME = "trq_stats"
    REQUIRED = ['module_type', 'worker', 'tenant_id', 'dscp', 'block']

    def __init__(self):

        super().__init__()

        # parameters
        self._block = None
        self._dscp = None

        # data structures
        self.trq_stats = {}

    def __eq__(self, other):

        return super().__eq__(other) and self.block == other.block

    @property
    def dscp(self):
        return self._dscp

    @dscp.setter
    def dscp(self, value):
        self._dscp = "{0:#0{1}x}".format(int(value, 16), 4)

    @property
    def block(self):
        return self._block

    @block.setter
    def block(self, value):

        if isinstance(value, ResourceBlock):

            self._block = value

        elif isinstance(value, dict):

            wtp = RUNTIME.wtps[EtherAddress(value['wtp'])]

            if 'hwaddr' not in value:
                raise ValueError("Missing field: hwaddr")

            if 'channel' not in value:
                raise ValueError("Missing field: channel")

            if 'band' not in value:
                raise ValueError("Missing field: band")

            if 'wtp' not in value:
                raise ValueError("Missing field: wtp")

            # Check if block is valid
            incoming = ResourceBlock(wtp, EtherAddress(value['hwaddr']),
                                     int(value['channel']),
                                     int(value['band']))

            match = [block for block in wtp.supports if block == incoming]

            if not match:
                raise ValueError("No block specified")

            if len(match) > 1:
                raise ValueError("More than one block specified")

            self._block = match[0]

    def to_dict(self):
        """ Return a JSON-serializable."""

        out = super().to_dict()

        out['block'] = self.block.to_dict()
        out['dscp'] = self.dscp
        out['trq_stats'] = {str(k): v for k, v in self.trq_stats.items()}

        return out

    def run_once(self):
        """ Send out request. """

        if self.tenant_id not in RUNTIME.tenants:
            self.log.info("Tenant %s not found", self.tenant_id)
            self.unload()
            return

        tenant = RUNTIME.tenants[self.tenant_id]
        wtp = self.block.radio

        if wtp.addr not in tenant.wtps:
            self.log.info("WTP %s not found", wtp.addr)
            self.unload()
            return

        if not wtp.connection or wtp.connection.stream.closed():
            self.log.info("WTP %s not connected", wtp.addr)
            self.unload()
            return

        self.log.info("Sending %s request to %s (id=%u)",
                      self.MODULE_NAME, wtp.addr, self.module_id)

        stats_req = Container(version=PT_VERSION,
                              type=PT_TRQ_STATS_REQUEST,
                              length=23+len(tenant.tenant_name),
                              seq=wtp.seq,
                              module_id=self.module_id,
                              hwaddr=self.block.hwaddr.to_raw(),
                              channel=self.block.channel,
                              band=self.block.band,
                              dscp=int(self.dscp, 16),
                              ssid=tenant.tenant_name.to_raw())

        msg = TRQ_STATS_REQUEST.build(stats_req)
        wtp.connection.stream.write(msg)

    def handle_response(self, response):
        """Handle an incoming TRQ_STATS_RESPONSE message.
        Args:
            response, a TRQ_STATS_RESPONSE message
        Returns:
            None
        """

        # update this object
        self.trq_stats = {
            'deficit_used': response.deficit_used,
            'tx_pkts': response.tx_pkts,
            'tx_bytes': response.tx_bytes,
            'max_queue_length': response.max_queue_length,
        }

        # call callback
        self.handle_callback(self)


class TRQStatsWorker(ModuleLVAPPWorker):
    """ Counter worker. """

    pass


def trq_stats(**kwargs):
    """Create a new module."""

    return RUNTIME.components[TRQStatsWorker.__module__].add_module(**kwargs)


def bound_trq_stats(self, **kwargs):
    """Create a new module (app version)."""

    kwargs['tenant_id'] = self.tenant.tenant_id
    return trq_stats(**kwargs)

setattr(EmpowerApp, TRQStats.MODULE_NAME, bound_trq_stats)


def launch():
    """ Initialize the module. """

    return TRQStatsWorker(TRQStats, PT_TRQ_STATS_RESPONSE, TRQ_STATS_RESPONSE)
