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

from empower.core.app import EmpowerApp
from empower.core.app import DEFAULT_PERIOD
import wifi_rssi_mcs_table as table
import copy
from empower.datatypes.etheraddress import EtherAddress

class AquametMobilityManager(EmpowerApp):
    """ Constants from the standard. 
    Extend this to find the standard being used and choose the appropriate value"""   
    WIFI_DIFS_B = 50 
    WIFI_SIFS_B = 10
    # To do: Find the right value for number of bytes in a MAC header
    ETH_HEADER_BYTES = 100
    WIFI_MAC_HEADER_BYTES = 20
    WIFI_PHY_HEADER_BYTES = 15
    # To do: Find the right value for number of bytes in an ACK control frame including all headers for it. 
    ACK_BYTES = 10 # this only includes the payload bytes in the ack frame.
    # The mac address of the client whose throughput is being monitored 
    # and hadover done based on attainable throughput
    tagged_sta_mac_addr='a4:34:d9:bf:50:ef'

    num_lvap_in_network = 0
    num_wtp_in_network = 0
    nif_stats_counter = 0
    bincounter_stats_counter = 0
    rssi_stats_counter = 0

    window_time = 500 # ms
    sliding_window_samples = 20
    tagged_lvap_sample_counter = 0
    global_window_counter = 0

    last_counters_stats = None
    last_nif_stats = None
    last_succ = 0
    last_att = 0
    last_acked_bytes = 0

    dl_frame_len_bytes={}#[lvap][sliding window]
    dl_arr_rate_pps={}#[lvap][sliding window]
    dl_num_active_clients={}#[wtp][sliding window]
    dl_active_clients=[]
    dl_pdr={}#[wtp,lvap][sliding window]
    dl_aggr_pdr={}#[wtp][sliding window]
    dl_est_rate={}#[wtp,lvap][sliding window]
    dl_meas_rate={}#[wtp,lvap][sliding window]
    dl_rssi={}#[wtp,lvap][sliding window]
    dl_meas_thput={}#[wtp,lvap][sliding window]
    dl_att_thput={}#[wtp,lvap][sliding window]

    dl_aggr_attempts={}#[wtp][sliding window]
    dl_aggr_succ={}#[wtp][sliding window]
    current_assoc_map={}# key=lvap val = wtp associated with
    #current_assoc_map={}# key = wtp, val = lvaps associated with it.

    new_wtps=[]
    new_lvaps=[] 

    def __init__(self, **kwargs):

        super().__init__(**kwargs)

        # Register an wtp up event
        self.wtpup(callback=self.wtp_up_callback)
        # Register a Sta joining the network
        self.lvapjoin(callback=self.lvap_join_callback)

    def wtp_up_callback(self, wtp):
        """Called when a new WTP connects to the controller."""
        self.new_wtps.append(wtp)

    def wtp_up_initialize(self) :
        for wtp in self.new_wtps : 
            self.num_wtp_in_network += 1
            self.dl_aggr_attempts[wtp.addr] = [0]*self.sliding_window_samples
            self.dl_aggr_succ[wtp.addr] = [0]*self.sliding_window_samples
            self.dl_aggr_pdr[wtp.addr] = [0]*self.sliding_window_samples
            # Add polling callback to this joined WTP
            # EAch wtp has 2 network interfaces, so I expect that there will be 
            # 2 blocks for each WTP.
            self.log.info("Number of blocks is ",len(wtp.supports))
            for block in wtp.supports:
                # UCQM has the avg and std of rssi values
                self.ucqm(block=block, every=self.window_time,
                    callback=self.rssi_callback)
                self.wifistats(block=block, every=self.window_time,
                    callback=self.wifi_stats_callback)            
                
            
    def lvap_join_callback(self, lvap):
        """ New LVAP. """
        self.new_lvaps.append(lvap)

    def lvap_join_initialize(self) :
        for lvap in self.new_lvaps :
            self.num_lvap_in_network += 1
            # Add polling callback to this joined lvap
            self.bin_counter(lvap=lvap.addr,
                            bins=[512, 1514, 8192],
                            every=self.window_time,
                            callback=self.counters_callback)
            self.nif_stats(lvap=lvap.addr,
                            every=self.window_time,
                            callback=self.nif_stats_callback)

    def rssi_callback(self, ucqm):
        """ New RSSI stats available. """ 
        self.log.info("New UCQM received from %s" % ucqm.block)
        self.rssi_stats_counter += 1
        #loop over the lvaps that this wtp has heard from
        ## fix
        # How do I identify a wtp with both its interfaces.
        # Because a wtp here is identofied by its mac address which is different for each interface
        wtp = ucqm.block.radio
        # How do I identify that 2 ucqm responses I get from the 2 different 
        # interfaces of the same WTP belong to the same WTP ?
        # I am not sure that this is the right rssi value to use. Over what time is this averaged over ? 
        for lvap_addr in ucqm.maps : 
            self.dl_rssi[wtp.addr,lvap_addr].insert(0,ucqm.maps[lvap_addr]['last_rssi_avg'])
            self.dl_est_rate[wtp.addr,lvap_addr].insert(0,
                        table.GetEstimatedSendingRateFromRssi(ucqm.maps[lvap_addr]['last_rssi_avg']))
            if len(self.dl_rssi[wtp.addr,lvap_addr]) > self.sliding_window_samples :
                del self.dl_rssi[wtp.addr,lvap_addr][self.sliding_window_samples:]
                del self.dl_est_rate[wtp.addr,lvap_addr][self.sliding_window_samples:]


    def counters_callback(self, stats) :
        """ New stats available. """
        self.log.info("New counters received from %s" % stats.lvap)
        self.bincounter_stats_counter += 1
        ## fix
        lvap = stats.lvap
        #wtp = stats.lvap.wtp

        # For each frame length I have a count. 
        # I am just going to add them all up  and average
        # The counters here seem to be upcounters.

        if self.last_counters_stats is not None :
            this_window_bytes = sum(stats.tx_bytes) - sum(self.last_counters_stats.tx_bytes)
            this_window_pkts = sum(stats.tx_packets) - sum(self.last_counters_stats.tx_packets)
            arr_pps =  float((this_window_pkts))*1000.0/self.window_time# pps
            avg_frame_len_bytes = float((this_window_bytes))/this_window_pkts

        else :
            arr_pps = sum(stats.tx_packets)*1000.0/self.window_time# pps
            avg_frame_len_bytes = float(sum(stats.tx_bytes))/sum(stats.tx_packets)
        
        self.last_counters_stats = copy.copy(stats)

        self.dl_arr_rate_pps[lvap.addr].insert(0,arr_pps)
        self.dl_frame_len_bytes[lvap.addr].insert(0,avg_frame_len_bytes- ETH_HEADER_BYTES)

        if len(self.dl_arr_rate_pps[lvap.addr]) > self.sliding_window_samples :
            del self.dl_arr_rate_pps[lvap.addr][self.sliding_window_samples:]
            del self.dl_frame_len_bytes[lvap.addr][self.sliding_window_samples:]


    def nif_stats_callback(self, nif):
        # This function is called periodically once for each lvap.
        # aggregate data here 
        self.nif_stats_counter += 1
        ## fix
        lvap = nif.lvap
        wtp = nif.lvap.wtp

        if (lvap.addr == EtherAddress(self.tagged_sta_mac_addr)) :
            self.tagged_lvap_sample_counter += 1

        succ = 0
        att = 0
        acked_bytes = 0
        
        for rate in nif.rates : 
            succ += nif.rates[rate]['hist_successes']
            att += nif.rates[rate]['hist_attempts']
            acked_bytes += nif.rates[rate]['hist_acked_bytes']

        tmp_succ = succ - self.last_succ
        tmp_att = att - self.last_att
        tmp_acked_bytes = acked_bytes - self.last_acked_bytes

        pdr = float(tmp_succ) / tmp_att    
        meas_thput_kbps = float(tmp_acked_bytes*8) / self.window_time
        self.dl_pdr[wtp.addr,lvap.addr].insert(0,pdr)
        self.dl_meas_thput[wtp.addr,lvap.addr].insert(0,meas_thput_kbps)
        self.dl_aggr_attempts[wtp.addr][0] += tmp_att 
        self.dl_aggr_succ[wtp.addr][0] += tmp_succ

        self.last_succ = succ
        self.last_att = att
        self.last_acked_bytes = acked_bytes
        self.last_nif_stats = copy.copy(nif)
        
        rate_with_max_attempts = 0
        max_att = 0
        for rate in self.nif.rates :
            if rate in self.last_nif_stats.rates :
                num_att = self.nif.rates[rate]['hist_attempts'] - self.last_nif_stats.rates[rate]['hist_attempts']
            else :
                num_att = self.nif.rates[rate]['hist_attempts']
                
            if num_att > max_attempts :
                max_attempts = num_att
                rate_with_max_attempts = rate

        self.dl_meas_rate[wtp.addr,lvap.addr].insert(0,rate_with_max_attempts)

        if len(self.dl_pdr[wtp.addr,lvap.addr]) > self.sliding_window_samples :
            del self.dl_pdr[wtp.addr,lvap.addr][self.sliding_window_samples:]
            del self.dl_meas_rate[wtp.addr,lvap.addr][self.sliding_window_samples:]
            del self.dl_meas_thput[wtp.addr,lvap.addr][self.sliding_window_samples:]

    def wifi_stats_callback(self, stats):



    # Evaluate for one wtp association set
    def nif_evaluate_stats(self, wtp_addr, wtp_assoc_set) :   
        self.dl_num_active_clients[wtp_addr] = [0]*self.sliding_window_samples
        self.dl_att_thput[wtp_addr,:] = [0]*self.sliding_window_samples
        for w in range(0,self.sliding_window_samples) :
            self.dl_active_clients = []
            # Find the number of active stations in this window.
            for lvap in wtp_assoc_set :    
                if self.dl_arr_rate_pps[lvap.addr][w] > 0.0 :
                    self.dl_active_clients.append(lvap.addr)
                    self.dl_num_active_clients[wtp_addr][w] += 1

            denominator = 0
            for wtp_addr in  wtp_assoc_set :
                self.dl_aggr_pdr[wtp_addr][w] = float(self.dl_aggr_succ[wtp_addr][w])/self.dl_aggr_attempts[wtp_addr][w]
                for lvap_addr in self.dl_active_clients :
                    ack_time = table.ack_time(table.GetEstimatedMcsFromRssi(self.dl_rssi[wtp_addr,lvap_addr][w]))
                    denominator += ( (self.dl_arr_rate_pps[lvap_addr][w]) \
                                    * (self.dl_frame_len_bytes[lvap_addr][w])) \
                                        /(self.dl_est_rate[wtp_addr,lvap_addr][w] + WIFI_DIFS_B \
                                            + WIFI_SIFS_B + (float(WIFI_MAC_HEADER_BYTES*8*1000)/self.dl_est_rate[wtp_addr,lvap_addr][w]) \
                                            + (float(WIFI_PHY_HEADER_BYTES*8*1000)/table.base_rate['WIFI_STD']) \
                                            + table.ack_time(self.dl_est_rate[wtp_addr,lvap_addr][w]))
        
            # Get stats from the first structure object which is the ue
            # whose attainable throughput is to be measured
            # If arrival rate is zero. i.e. it is not an active client then these eq. will give troughput = 0
            for lvap in wtp_assoc_set :                 
                thput_unsat = ( (self.dl_arr_rate_pps[lvap_addr][w]) \
                                * (self.dl_frame_len_bytes[lvap_addr][w]) \
                                * self.dl_aggr_pdr[wtp_addr][w] * 8.0) / 1000.0
                thput_sat = ( (self.dl_arr_rate_pps[lvap_addr][w]) \
                            * (self.dl_frame_len_bytes[lvap_addr][w]) \
                            * self.dl_aggr_pdr[wtp_addr][w] * 8.0 * 1000.0) / denominator
                self.dl_att_thput[wtp_addr,lvap_addr][w] = min(thput_sat, thput_unsat)

    def loop(self):
        """ Periodic job. """
        # Add callbacks for the new WTPs and LVAPs that 
        # have joined the network since last loop periodic trigger
        self.wtp_up_initialize()
        # reset this list
        self.new_wtps=[]
        self.lvap_join_initialize()        
        # reset this list
        self.new_lvaps=[]

        self.global_window_counter += 1
        wtp_assoc_set=[]
        # find the lvap using sta mac addr. ??
        ## fix
        ## corrected
        # This is a dictionary of all the lvaps currently in the network.
        all_lvaps = self.lvaps()
        # This is the EtherAddress object for the specified mac address. 
        tagged_lvap_etherAddr_obj = EtherAddress(self.tagged_sta_mac_addr)
        # Proceed further only if the lvap I am interested in following has joined the network 
        if all_lvaps.get(tagged_lvap_etherAddr_obj) is not None :
            # This is the EtherAddress object for the specified mac address. 
            tagged_lvap = all_lvaps[tagged_lvap_etherAddr_obj]
            tagged_lvap_curr_assoc_wtp = tagged_lvap.wtp
            best_target_wtp = tagged_lvap_current_association
            if self.tagged_lvap_sample_counter >= self.sliding_window_samples :
                self.dl_meas_prob_good_thput[tagged_lvap_curr_assoc_wtp.addr,tagged_lvap.addr] = \
                                        (sum(i >= self.thput_threshold \
                                            for i in self.dl_meas_thput[tagged_lvap_curr_assoc.addr,tagged_lvap.addr])
                                            /float(self.sliding_window_samples))
                #if self.dl_meas_prob_good_thput[tagged_lvap_curr_assoc_wtp.addr,tagged_lvap.addr] < self.tolerance_prob :
                association_changed_flag = False
                max_prob_satisfying_qos = self.dl_meas_prob_good_thput[tagged_lvap_curr_assoc_wtp.addr,tagged_lvap.addr]       
                for wtp in self.wtps() :
                    # If it is not then trigger the task of finding a new one, 
                    # by iterating through all the association options.
                    # pick the one that is best after iterating through all of them. 
                    if wtp.addr != tagged_lvap_curr_assoc_wtp.addr :
                        # Evaluate attainable throughput if tagged sta is moved ot this wtp   
                        # All lvaps associated with that wtp
                        for block in wtp.supports:
                            # I am assuming that of all the blocks only 1 block has lvaps on it. 
                            # the others will return NOne for lvaps
                            if self.lvaps(block=block) not None:
                                # This is the block on which lvaps are scheduled
                                wtp_assoc_set = copy.copy(self.lvaps(block=block))
                                wtp_assoc_set.append(tagged_lvap)
                                self.nif_evaluate_stats(wtp.addr, wtp_assoc_set)

                        prob_satisfying_qos = \
                                    (sum(i >= self.thput_threshold \
                                        for i in self.dl_att_thput[wtp.addr,tagged_lvap.addr])
                                        /float(self.sliding_window_samples))
                        if prob_satisfying_qos > max_prob_satisfying_qos : 
                            # After this evaluation I need to see if this association set is a fit for the tagged sta.
                            max_prob_satisfying_qos = prob_satisfying_qos
                            best_target_wtp = wtp
                            association_changed_flag = True

                # I shall now use this wtp with the least prob of violating 
                # qos and associate the tagged lvap with this wtp. 

                # This is supposed to trigger the handover.
                if association_changed_flag : 
                    tagged_lvap.wtp = wtp_with_min_qos_violation_prob
                    # Reset counters and the measured throughput window since these values cannot be used anymore. 
                    self.tagged_lvap_sample_counter = 0
                    self.dl_meas_thput[tagged_lvap_curr_assoc_wtp.addr, tagged_lvap.addr]=[]


        # Reset the things I need to after each loop or each Wm
        for wtp in self.wtps() :
            self.dl_aggr_attempts[wtp.addr] = circular shift right 
            self.dl_aggr_succ[wtp.addr] = circular shift right
            self.dl_aggr_attempts[wtp.addr][0] = 0 
            self.dl_aggr_succ[wtp.addr][0] = 0


def launch(tenant_id, every=DEFAULT_PERIOD):
    """ Initialize the module. """
    return AquametMobilityManager(tenant_id=tenant_id, every=500)
