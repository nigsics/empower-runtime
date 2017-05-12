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

"""UE handover Module."""

from protobuf_to_dict import protobuf_to_dict
from empower.vbsp import PRT_CTRL_COMMANDS
from empower.vbsp import UE_HANDOVER_CAUSE
from empower.vbsp.messages import commands_pb2
from empower.vbsp.messages import main_pb2
from empower.core.vbs import VBS
from empower.datatypes.etheraddress import EtherAddress
from empower.vbsp.vbspserver import ModuleVBSPWorker
from empower.core.module import ModuleTrigger
from empower.vbsp.vbspconnection import create_header
from empower.core.utils import ether_to_hex
from empower.main import RUNTIME

class UEHandover(ModuleTrigger):
    """ UEHandover object. """

    MODULE_NAME = "ue_handover"
    REQUIRED = ['module_type', 'worker', 'tenant_id', 'ue', 'ho_param']

    def __init__(self):

        ModuleTrigger.__init__(self)

        # parameters
        self._ue = None
        self._ho_param = None
        self._reply = None

    @property
    def ue(self):
        """Return UE."""

        return self._ue

    @ue.setter
    def ue(self, value):
        """Set UE."""

        self._ue = value

    @property
    def ho_param(self):
        """Return UE handover request."""

        return self._ho_param

    @ho_param.setter
    def ho_param(self, value):
        """Set UE handover request."""

        if self.ho_param:
            raise ValueError("Cannot update request configuration")

        vbses = RUNTIME.tenants[self.tenant_id].vbses

        if "src_vbs" not in value:
            raise ValueError("Missing source VBS")

        if "dst_vbs" not in value:
            raise ValueError("Missing destination VBS")

        if EtherAddress(value["src_vbs"]) not in vbses:
            raise ValueError("Invalid source vbs parameter")

        src_vbs = EtherAddress(value["src_vbs"])

        if "src_cell_id" not in value:
            raise ValueError("Missing source cell id")

        cell_present = 0
        for cell in vbses[src_vbs].cells:
            if value["src_cell_id"] == cell["phys_cell_id"]:
                cell_present = 1
                break

        if not cell_present:
            raise ValueError("Invalid source cell id")

        if EtherAddress(value["dst_vbs"]) not in vbses:
            raise ValueError("Invalid destination vbs parameter")

        dst_vbs = EtherAddress(value["dst_vbs"])

        if "dst_cell_id" not in value:
            raise ValueError("Missing destination cell id")

        cell_present = 0
        for cell in vbses[dst_vbs].cells:
            if value["dst_cell_id"] == cell["phys_cell_id"]:
                cell_present = 1
                break

        if not cell_present:
            raise ValueError("Invalid destination cell id")

        if "cause" not in value:
            raise ValueError("Missing handover cause")

        if value["cause"] not in UE_HANDOVER_CAUSE:
            raise ValueError("Invalid handover cause")

        self._ho_param = value

    @property
    def reply(self):
        """Return response for UE handover request."""

        return self._reply

    @reply.setter
    def reply(self, response):
        """Set response for UE handover request."""

        self._reply = protobuf_to_dict(response)

        event_type = response.WhichOneof("event_types")
        self._reply = self._reply[event_type][PRT_CTRL_COMMANDS]["repl"]

        if self._reply["cmd_status"] != commands_pb2.CTRLCMDST_SUCCESS:
            return

    def __eq__(self, other):

        return super().__eq__(other) and False

    def to_dict(self):
        """ Return a JSON-serializable."""

        out = super().to_dict()

        out['ue'] = self.ue
        out['tenant'] = self.tenant_id
        out['ho_param'] = self.ho_param
        out['reply'] = self.reply

        return out

    def run_once(self):
        """Send out UE handover request."""

        if self.tenant_id not in RUNTIME.tenants:
            self.log.info("Tenant %s not found", self.tenant_id)
            self.unload()
            return

        tenant = RUNTIME.tenants[self.tenant_id]

        ho_param = self.ho_param

        src_vbs = EtherAddress(ho_param["src_vbs"])

        ue_addr = (src_vbs, self.ue)

        if ue_addr not in tenant.ues:
            self.log.info("UE %s not found", ue_addr)
            return

        vbses = RUNTIME.tenants[self.tenant_id].vbses

        if src_vbs not in vbses:
            return

        vbs = vbses[src_vbs]

        if not vbs.connection or vbs.connection.stream.closed():
            self.log.info("VBS %s not connected", vbs.addr)
            return

        ctrl_cmds = main_pb2.emage_msg()

        enb_id = ether_to_hex(src_vbs)

        create_header(self.module_id, enb_id, ctrl_cmds.head)

        # Creating a single event message to send UE handover request
        event_type_msg = ctrl_cmds.se

        ctrl_cmds_msg = event_type_msg.mCtrl_cmds
        ctrl_cmds_req_msg = ctrl_cmds_msg.req

        ho_req = ctrl_cmds_req_msg.ctrl_ho

        ho_req.rnti = self.ue
        ho_req.s_cell_id = ho_param["src_cell_id"]
        ho_req.s_eNB_id = enb_id
        ho_req.t_cell_id = ho_param["dst_cell_id"]
        ho_req.t_eNB_id = ether_to_hex(EtherAddress(ho_param["dst_vbs"]))
        ho_req.cause = UE_HANDOVER_CAUSE[ho_param["cause"]]

        connection = vbs.connection

        self.log.info("Sending UE handover req to %s (id=%u)", vbs.addr,
                      self.module_id)

        vbs.connection.stream_send(ctrl_cmds)

    def handle_response(self, response):
        """Handle an incoming response message for handover request.
        Args:
            message, a response message for handover request
        Returns:
            None
        """

        # update cache
        self.reply = response

        # call callback
        self.handle_callback(self)


class UEHandoverWorker(ModuleVBSPWorker):
    """ UEHandoverWorker worker. """

    pass


def ue_handover(**kwargs):
    """Create a new module."""

    return \
        RUNTIME.components[UEHandoverWorker.__module__].add_module(**kwargs)


def bound_ue_handover(self, **kwargs):
    """Create a new module (app version)."""

    kwargs['tenant_id'] = self.tenant.tenant_id
    return ue_handover(**kwargs)

setattr(VBS, UEHandover.MODULE_NAME, bound_ue_handover)


def launch():
    """ Initialize the module. """

    return UEHandoverWorker(UEHandover, PRT_CTRL_COMMANDS)