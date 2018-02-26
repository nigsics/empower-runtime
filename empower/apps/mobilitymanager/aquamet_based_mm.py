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

class AquametMobilityManager(EmpowerApp):
    """
    Command Line Parameters:

        tenant_id: tenant id
        every: loop period in ms (optional, default 5000ms)

    Example:

        ./empower-runtime.py apps.mobilitymanager.proactivemm \
            --tenant_id=52313ecb-9d00-4b7d-b873-b55d3d9ada26
    """
    # this is not the right place for this. Move it later. 
    WIFI_DIFS_B = 50 
    WIFI_SIFS_B = 10
    T_HEADER = 100

    num_lvap_in_network = 0
    num_wtp_in_network = 0
    nif_stats_counter = 0
    bincounter_stats_counter = 0
    rssi_stats_counter = 0

    window_time = 500 # ms
    sliding_window_samples = 20
    thput_threshold = 2000 #kbps
    tolerance_prob = 0.7

    last_counters_stats = None
    last_nif_stats = None
    last_succ = 0
    last_att = 0
    last_acked_bytes = 0

    dl_frame_len_bytes={}#[wtp,lvap][sliding window]
    dl_arr_rate_pps={}#[wtp,lvap][sliding window]
    dl_num_active_clients={}#[wtp][sliding window]
    dl_active_clients=[]
    dl_pdr={}#[wtp,lvap][sliding window]
    dl_aggr_pdr={}#[wtp][sliding window]
    dl_est_rate={}#[wtp,lvap][sliding window]
    dl_meas_rate={}#[wtp,lvap][sliding window]
    dl_rssi={}#[wtp,lvap][sliding window]
    dl_meas_thput={}#[wtp,lvap][sliding window]
    dl_att_thput={}#[wtp,lvap][sliding window]
    dl_meas_prob_good_thput={}#[wtp,lvap]

    def __init__(self, **kwargs):

        super().__init__(**kwargs)

        # Register an wtp up event
        self.wtpup(callback=self.wtp_up_callback)
        # Register a Sta joining the network
        self.lvapjoin(callback=self.lvap_join_callback)

    def wtp_up_callback(self, wtp):
        """Called when a new WTP connects to the controller."""
        num_wtp_in_network += 1
        # Add polling callback to this joined WTP
        for block in wtp.supports:
            self.ucqm(block=block, every=self.window_time,
                callback=self.rssi_callback)
                # UCQM has the avg and std of rssi values
    
    def rssi_callback(self, ucqm):
        """ New stats available. """
        # How often is this called. 
        # Do I get one message per wtp . Yes because it is called in the wtpup function. 
        self.log.info("New UCQM received from %s" % ucqm.block)
        rssi_stats_counter += 1
        #loop over th elvaps that this wtp has hear from
        wtp = ucqm.wtp 
        for lvap in ucqm.maps : 
            self.dl_est_rate[wtp,lvap] = \
                        table.GetEstimatedSendingRateFromRssi(ucqm.maps[lvap]['last_rssi_avg'])
        # Do I have to use the concept of block to access the avg and std rssi ?
            

    def lvap_join_callback(self, lvap):
        """ New LVAP. """
        num_lvap_in_network += 1
        # Add polling callback to this joined lvap
        self.bin_counter(lvap=lvap.addr,
                        bins=[512, 1514, 8192],
                        every=self.window_time,
                        callback=self.counters_callback)
        self.nif_stats(lvap=lvap.addr,
                        every=self.window_time,
                        callback=self.nif_stats_callback)

    def counters_callback(self, stats) :
        """ New stats available. """
        self.log.info("New counters received from %s" % stats.lvap)
        self.bincounter_stats_counter += 1
        lvap = stats.lvap
        wtp = stats.lvap.wtp()

        # For each frame length I have a count. 
        # I am just going to add them all up  and average
        # The counters here seem to be upcounters.

        temp_stats = stats
        if self.last_counters_stats is not None :
            this_window_bytes = sum(stats.tx_bytes) - sum(self.last_counters_stats.tx_bytes)
            this_window_pkts = sum(stats.tx_packets) - sum(self.last_counters_stats.tx_packets)
            arr_pps =  (float)(this_window_pkts)*1000/self.window_time# pps
            avg_frame_len_bytes = (float)(this_window_bytes)/this_window_pkts

        else :
            arr_pps = sum(stats.tx_packets)*1000/self.window_time# pps
            avg_frame_len_bytes = sum(stats.tx_bytes)/sum(stats.tx_packets)
        
        self.last_counters_stats = stats

        self.dl_arr_rate_pps[wtp,lvap].insert(0,arr_pps)
        self.dl_frame_len_bytes[wtp,lvap].insert(0,avg_frame_len_bytes)

        if len(self.dl_arr_rate_pps[wtp,lvap]) > self.sliding_window_samples :
            del self.dl_arr_rate_pps[wtp,lvap][self.sliding_window_samples:]
            del self.dl_frame_len_bytes[wtp,lvap][self.sliding_window_samples:]


    def nif_stats_callback(self, nif):
        # This function is called periodically once for each lvap.
        # aggregate data here 
        self.nif_stats_counter += 1
        lvap = nif.lvap
        wtp = nif.lvap.wtp()

        succ = 0
        att = 0
        acked_bytes = 0
        
        for rate in self.nif.rates : 
            succ += self.nif.rates[rate]['hist_successes']
            att += self.nif.rates[rate]['hist_attempts']
            acked_bytes += self.nif.rates[rate]['hist_acked_bytes']

        tmp_succ = succ - self.last_succ
        tmp_att = att - self.last_att
        tmp_acked_bytes = acked_bytes - self.last_acked_bytes

        pdr = (float)(tmp_succ) / tmp_att    
        meas_thput_kbps = (float)(tmp_acked_bytes*8) / self.window_time
        self.dl_pdr[wtp,lvap].insert(0,pdr)
        self.dl_meas_thput[wtp,lvap].insert(0,meas_thput_kbps)

        self.last_succ = succ
        self.last_att = att
        self.last_acked_bytes = acked_bytes
        self.last_nif_stats = nif
        

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

        self.dl_meas_rate[wtp,lvap].insert(0,rate_with_max_attempts)



        if len(self.dl_pdr[wtp,lvap]) > self.sliding_window_samples :
            del self.dl_pdr[wtp,lvap][self.sliding_window_samples:]
            del self.dl_meas_rate[wtp,lvap][self.sliding_window_samples:]
            del self.dl_meas_thput[wtp,lvap][self.sliding_window_samples:]

        # I have collected all the nif stats for all the lvaps in the network for this window.
        # I can now begin to evaluate them by estimaitng attainable throughput  
        #if self.nif_stats_counter >= self.num_lvap_in_network :
        #    self.nif_stats_counter = 0
            




    def nif_evaluate_stats(self, wtp_assoc_set) :    
        for w in range(0,self.sliding_window_samples) :
            # Assume the estimation of rate form rssi is already done in the rssi callback function 
            # Find the number of active stations in this window.
            for wtp in wtp_assoc_set :
                for lvap in wtp_assoc_set[wtp] :    
                    if self.dl_arr_rate_pps[wtp,lvap][w] > 0.0 :
                        self.dl_active_clients[wtp].append(lvap)
                        self.dl_num_active_clients[wtp,lvap] += 1

            denominator = 0
            for wtp in  wtp_assoc_set :
                for lvap in self.dl_active_clients[wtp] :
                    denominator += ( (self.dl_arr_rate_pps[wtp,lvap][w]) \
                                    * (self.dl_frame_len_bytes[wtp,lvap][w])) \
                                        /(self.dl_est_rate[wtp,lvap][w] + WIFI_DIFS_B + WIFI_SIFS_B + T_HEADER \
                                            + table.ack_time(self.dl_est_rate[wtp,lvap][w]))
        
            # Get stats from the first structure object which is the ue
            # whose attainable throughput is to be measured

            thput_unsat = ( (self.dl_arr_rate_pps[wtp,lvap][w]) \
                                * (self.dl_frame_len_bytes[wtp,lvap][w]) \
                                * self.dl_pdr[wtp,lvap][w] * 8.0) / 1000.0
            thput_sat = ( (self.dl_arr_rate_pps[wtp,lvap][w]) \
                            * (self.dl_frame_len_bytes[wtp,lvap][w]) \
                            * self.dl_pdr[wtp,lvap][w] * 8.0 * 1000.0) / denominator
            self.dl_att_thput[wtp,lvap][w] = min(thput_sat, thput_unsat)

    def loop(self):
        """ Periodic job. """
        wtp_assoc_set={}
        ## fix
        # create an lvap object using mac address of the stattion that is being observed
        tagged_lvap = ''
        tagged_lvap_current_association = tagged_lvap.wtp()
        min_qos_violation_prob = 1.0
        wtp_with_min_qos_violation_prob = tagged_lvap_current_association
        if len(self.dl_frame_len_bytes) >= self.sliding_window_samples :
            self.dl_meas_prob_good_thput[tagged_lvap_current_association,tagged_lvap] = \
                                    (sum(i >= self.thput_threshold \
                                        for i in self.dl_meas_thput[tagged_lvap_current_association,tagged_lvap])
                                        /float(self.sliding_window_samples))
            if self.dl_meas_prob_good_thput[tagged_lvap_current_association,tagged_lvap] < self.tolerance_prob :
                for wtp in self.wtps() :
                    # If it is not then trigger the task of finding a new one, 
                    # by iterating through all the association options.
                    # pick the one that is best after iterating through all of them. 
                    if wtp != tagged_lvap_current_association :
                        # Evaluate attainable throughput if tagged sta is moved ot this wtp   
                        wtp_assoc_set[wtp] = wtp.lvaps()
                        wtp_assoc_set[wtp].append(tagged_lvap)
                        self.nif_evaluate_stats(wtp_assoc_set[wtp])
                        violation_prob = \
                                    (sum(i >= self.thput_threshold \
                                        for i in self.dl_att_thput[wtp,tagged_lvap])
                                        /float(self.sliding_window_samples))
                        if violation_prob < min_qos_violation_prob : 
                            # After this evaluation I need to see if this association set is a fit for the tagged sta.
                            min_qos_violation_prob = violation_prob
                            wtp_with_min_qos_violation_prob = wtp

                # I shall now use this wtp with the least prob of violating 
                # qos and associate the tagged lvap with this wtp. 
                tagged_lvap.wtp = wtp_with_min_qos_violation_prob

        # RSSI based handover 
        #for lvap in self.lvaps():
        #    lvap.blocks = self.blocks().sortByRssi(lvap.addr).first()


def launch(tenant_id, every=DEFAULT_PERIOD):
    """ Initialize the module. """

    return AquametMobilityManager(tenant_id=tenant_id, every=500)
