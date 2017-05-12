#!/usr/bin/env python3
#
# Copyright (c) 2016 Supreeth Herle
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

"""VBS RRC Stats Module."""

from protobuf_to_dict import protobuf_to_dict
from empower.vbsp.messages import statistics_pb2
from empower.vbsp.messages import main_pb2
from empower.core.vbs import VBS
from empower.datatypes.etheraddress import EtherAddress
from empower.vbsp.vbspserver import ModuleVBSPWorker
from empower.core.module import ModuleTrigger
from empower.vbs_stats import VBS_CELL_STATS_TYPE
from empower.vbs_stats import REQ_EVENT_TYPE
from empower.vbs_stats import PRT_VBSP_CELL_STATS
from empower.vbsp.vbspconnection import create_header
from empower.core.utils import ether_to_hex
from empower.events.vbsdown import vbsdown
from empower.main import RUNTIME


class VBSCellStats(ModuleTrigger):
    """ VBSCellStats object. """

    MODULE_NAME = "vbs_cell_stats"
    REQUIRED = ['module_type', 'worker', 'tenant_id', 'vbs', 'cell', 'stats_req']

    def __init__(self):

        ModuleTrigger.__init__(self)

        # parameters
        self._vbs = None
        self._cell = None
        self._stats_req = None
        self._stats_reply = None

        vbsdown(tenant_id=self.tenant_id, callback=self.vbs_down_callback)

    def vbs_down_callback(self, vbs):
        """Called when an VBS disconnects from a tenant."""

        # Removes VBS from list of active VBSs
        worker = RUNTIME.components[VBSCellStatsWorker.__module__]

        module_ids = []
        module_ids.extend(worker.modules.keys())

        for module_id in module_ids:
            # Module object
            m = worker.modules[module_id]
            # Remove all the module pertaining to disconnected VBS
            if EtherAddress(m.vbs) == vbs.addr:
                m.cleanup()
                m.unload()

    @property
    def cell(self):
        """Return Cell ID."""

        return self._cell

    @cell.setter
    def cell(self, value):
        """Set Cell ID."""

        self._cell = value

    @property
    def vbs(self):
        """Return VBS."""

        return self._vbs

    @vbs.setter
    def vbs(self, value):
        """Set VBS."""

        vbses = RUNTIME.tenants[self.tenant_id].vbses

        if EtherAddress(value) not in vbses:
            raise ValueError("Invalid vbs parameter")

        self._vbs = EtherAddress(value)

    @property
    def stats_req(self):
        """Return request given for Cell statistics of VBS."""

        return self._stats_req

    @stats_req.setter
    def stats_req(self, value):
        """Set request parameter given for Cell statistics of VBS."""

        if self.stats_req:
            raise ValueError("Cannot update request configuration")

        if "stats_type" not in value:
            raise ValueError("Missing cells statistics type")

        if value["stats_type"] not in VBS_CELL_STATS_TYPE:
            raise ValueError("Invalid cells statistics type")

        if "event_type" not in value:
            raise ValueError("Missing event type (trigger, schedule, single)")

        if value["event_type"] not in REQ_EVENT_TYPE:
            raise ValueError("Invalid event type (trigger, schedule, single)")

        if value["event_type"] == "schedule" and "periodicity" not in value:
            raise ValueError("Missing periodicity for scheduled event")

        self._stats_req = value

    @property
    def stats_reply(self):
        """Return Cell statistics reply."""

        return self._stats_reply

    @stats_reply.setter
    def stats_reply(self, response):
        """Set Cell statistics reply."""

        self._stats_reply = protobuf_to_dict(response)
        reply = protobuf_to_dict(response)

        vbses = RUNTIME.tenants[self.tenant_id].vbses

        if self.vbs not in vbses:
            return

        vbs = vbses[self.vbs]

        if not vbs.cell_stats:
           vbs.cell_stats = {}

        event_type = response.WhichOneof("event_types")
        stats = reply[event_type][PRT_VBSP_CELL_STATS]["repl"]
        self._stats_reply = \
                    self._stats_reply[event_type][PRT_VBSP_CELL_STATS]["repl"]

        if stats["status"] != statistics_pb2.SREQS_SUCCESS:
            return

        if "prb_utilz" in stats:
            vbs.cell_stats[self.cell] = stats["prb_utilz"]

    def __eq__(self, other):

        return super().__eq__(other) and self.vbs == other.vbs and \
            self.cell == other.cell and self.stats_req == other.stats_req

    def to_dict(self):
        """ Return a JSON-serializable."""

        out = super().to_dict()

        out['vbs'] = self.vbs
        out['tenant'] = self.tenant_id
        out['cell'] = self.cell
        out['stats_req'] = self.stats_req
        out['stats_reply'] = self.stats_reply

        return out

    def run_once(self):
        """Send out Cell statistics request."""

        if self.tenant_id not in RUNTIME.tenants:
            self.log.info("Tenant %s not found", self.tenant_id)
            self.unload()
            return

        vbses = RUNTIME.tenants[self.tenant_id].vbses

        if self.vbs not in vbses:
            return

        vbs = vbses[self.vbs]

        tenant = RUNTIME.tenants[self.tenant_id]

        if not vbs.connection or vbs.connection.stream.closed():
            self.log.info("VBS %s not connected", vbs.addr)
            return

        cell_present = 0
        for cell in vbs.cells:
            if self.cell == cell["phys_cell_id"]:
                cell_present = 1
                break

        if not cell_present:
            raise ValueError("Invalid Cell Id")

        st_req = self.stats_req

        vbs_cell_stats_req = main_pb2.emage_msg()

        enb_id = ether_to_hex(self.vbs)

        create_header(self.module_id, enb_id, vbs_cell_stats_req.head)

        # Creating a message to fetch VBS cell statistics
        event_type_msg = None
        if st_req["event_type"] == "trigger":
            event_type_msg = vbs_cell_stats_req.te
            event_type_msg.action = main_pb2.EA_ADD
        elif st_req["event_type"] == "schedule":
            event_type_msg = vbs_cell_stats_req.sche
            event_type_msg.action = main_pb2.EA_ADD
            event_type_msg.interval = st_req["periodicity"]
        else:
            event_type_msg = vbs_cell_stats_req.se

        vbs_cell_stats_msg = event_type_msg.mCell_stats
        vbs_cell_stats_req_msg = vbs_cell_stats_msg.req

        vbs_cell_stats_req_msg.cell_id = self.cell
        vbs_cell_stats_req_msg.cell_stats_types =  \
                                    VBS_CELL_STATS_TYPE[st_req["stats_type"]]

        connection = vbs.connection

        self.log.info("Sending Cell statistics req to %s (id=%u)", vbs.addr,
                      self.module_id)

        vbs.connection.stream_send(vbs_cell_stats_req)

    def cleanup(self):
        """Remove this module."""

        self.log.info("Cleanup %s (id=%u)", self.module_type, self.module_id)

        vbses = RUNTIME.tenants[self.tenant_id].vbses

        if self.vbs not in vbses:
            return

        vbs = vbses[self.vbs]

        tenant = RUNTIME.tenants[self.tenant_id]

        if not vbs.connection or vbs.connection.stream.closed():
            self.log.info("VBS %s not connected", vbs.addr)
            return

        st_req = self.stats_req

        vbs_cell_stats_req = main_pb2.emage_msg()

        enb_id = ether_to_hex(self.vbs)

        create_header(self.module_id, enb_id, vbs_cell_stats_req.head)

        # Creating a message to delete VBS cell statistics trigger or schedule
        # event
        event_type_msg = None
        if st_req["event_type"] == "trigger":
            event_type_msg = vbs_cell_stats_req.te
            event_type_msg.action = main_pb2.EA_DEL
        elif st_req["event_type"] == "schedule":
            event_type_msg = vbs_cell_stats_req.sche
            event_type_msg.action = main_pb2.EA_DEL
        else:
            return

        vbs_cell_stats_msg = event_type_msg.mCell_stats
        vbs_cell_stats_req_msg = vbs_cell_stats_msg.req

        vbs_cell_stats_req_msg.cell_id = self.cell
        vbs_cell_stats_req_msg.cell_stats_types =  \
                                    VBS_CELL_STATS_TYPE[st_req["stats_type"]]

        connection = vbs.connection

        vbs.connection.stream_send(vbs_cell_stats_req)

    def handle_response(self, response):
        """Handle an incoming stats response message.
        Args:
            message, a stats response message
        Returns:
            None
        """

        # update cache
        self.stats_reply = response

        # call callback
        self.handle_callback(self)


class VBSCellStatsWorker(ModuleVBSPWorker):
    """ VBSCellStatsWorker worker. """

    pass


def vbs_cell_stats(**kwargs):
    """Create a new module."""

    return \
        RUNTIME.components[VBSCellStatsWorker.__module__].add_module(**kwargs)


def bound_vbs_cell_stats(self, **kwargs):
    """Create a new module (app version)."""

    kwargs['tenant_id'] = self.tenant.tenant_id
    kwargs['vbs'] = self.addr
    return vbs_cell_stats(**kwargs)

setattr(VBS, VBSCellStats.MODULE_NAME, bound_vbs_cell_stats)


def launch():
    """ Initialize the module. """

    return VBSCellStatsWorker(VBSCellStats, PRT_VBSP_CELL_STATS)