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
import empower.apps.mobilitymanager.wifi_rssi_mcs_table as table
import copy
from empower.datatypes.etheraddress import EtherAddress
import time

from empower.main import RUNTIME

class AquametMobilityManager(EmpowerApp):

    # The mac address of the client whose throughput is being monitored 
    # and hadover done based on attainable throughput
    #tagged_sta_mac_addr='00:21:6A:71:F6:EA'
    tagged_sta_mac_addr = 'A4:34:D9:BF:50:EF'

    num_lvap_in_network = 0
    num_wtp_in_network = 0
    nif_stats_counter = 0
    bincounter_stats_counter = 0
    rssi_stats_counter = 0

    window_time = 500 # ms
    sliding_window_samples = 20
    tagged_lvap_sample_counter = 0
    other_lvap_sample_counter = 0
    global_window_counter = 0
    thput_threshold = 14000 #threshold in Kbps
    tolerance_prob = 0.7 # tolernace probability

    last_counters_stats = {}#[lvap]
    last_nif_stats = {}#[lvap]
    last_succ = {}#[lvap]
    last_att = {}#[lvap]
    last_acked_bytes = {}#[lvap]

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
    dl_meas_prob_good_thput = {}

    dl_aggr_attempts={}#[wtp][sliding window]
    dl_aggr_succ={}#[wtp][sliding window]
    current_assoc_map={}# key=lvap val = wtp associated with

    new_wtps=[]
    new_lvaps=[] 

    def __init__(self, **kwargs):

        super().__init__(**kwargs)

        self.log.manager.loggerDict['tornado.access'].disabled = True
        #self.log.manager.loggerDict['lvapp.lvappconnection'].disabled = True
        # Register an wtp up event
        self.wtpup(callback=self.wtp_up_callback)
        # Register a Sta joining the network
        self.lvapjoin(callback=self.lvap_join_callback)

    def wtp_up_callback(self, wtp):
        """Called when a new WTP connects to the controller."""
        self.new_wtps.append(wtp)
        self.log.info("windNum: " + str(self.global_window_counter) +
            " wtp:" + str(wtp.addr) + " just joined the network")



    def wtp_up_initialize(self) :
        for wtp in self.new_wtps : 
            self.num_wtp_in_network += 1
            self.dl_aggr_attempts[wtp.addr] = [0]*self.sliding_window_samples
            self.dl_aggr_succ[wtp.addr] = [0]*self.sliding_window_samples
            self.dl_aggr_pdr[wtp.addr] = [0]*self.sliding_window_samples


            for block in wtp.supports:
                # UCQM has the avg and std of rssi values
                self.ucqm(block=block, every=self.window_time,
                    callback=self.rssi_callback)
                self.wifistats(block=block, every=self.window_time,
                    callback=self.wifi_stats_callback)
                
            
    def lvap_join_callback(self, lvap):
        """ New LVAP. """
        self.new_lvaps.append(lvap)
        self.log.info("windNum: " + str(self.global_window_counter) +
            " lvap:" + str(lvap.addr) + " just joined the network")
        if lvap.addr == EtherAddress(self.tagged_sta_mac_addr):
            self.tagged_lvap_sample_counter = 0
        else:
            self.other_lvap_sample_counter = 0
        # create initializers for all the stats that need to be maintained for this lvap.
        self.dl_rssi[lvap.wtp.addr, lvap.addr] = []
        self.dl_frame_len_bytes[lvap.addr] = []  # [lvap][sliding window]
        self.dl_arr_rate_pps[lvap.addr] = [] # [lvap][sliding window]
        self.dl_meas_thput[lvap.wtp.addr, lvap.addr] = []
        self.dl_pdr[lvap.wtp.addr, lvap.addr] = []
        self.dl_est_rate[lvap.wtp.addr, lvap.addr] = []



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
        self.log.info("windNum: " + str(self.global_window_counter) +
           " rssi ucqm msg recv from wtp: " + str(ucqm.block.radio.addr) +
           " from interface: " + str(ucqm.block.hwaddr) +
           " on channel: " + str(ucqm.block.channel) + str(time.time()))

        self.rssi_stats_counter += 1
        #loop over the lvaps that this wtp has heard from
        wtp = ucqm.block.radio
        for lvap_addr in ucqm.maps :
            if (wtp.addr,lvap_addr) not in self.dl_rssi:
                self.dl_rssi[wtp.addr, lvap_addr] = []
            self.dl_rssi[wtp.addr,lvap_addr].insert(0,ucqm.maps[lvap_addr]['last_rssi_avg'])
            if lvap_addr == EtherAddress(self.tagged_sta_mac_addr):
                self.log.info("windNum: " + str(self.global_window_counter) +
                    " last_rssi_avg between wtp " + str(wtp.addr) +
                    " and lvap " + str(lvap_addr) +
                    " is " + str(self.dl_rssi[wtp.addr,lvap_addr][0]))

            if (wtp.addr,lvap_addr) not in self.dl_est_rate:
                self.dl_est_rate[wtp.addr, lvap_addr] = []

            self.dl_est_rate[wtp.addr,lvap_addr].insert(0,
                        table.GetEstimatedSendingRateFromRssi(ucqm.maps[lvap_addr]['last_rssi_avg']))
            #self.log.info("windNum: " + str(self.global_window_counter) +
            #" est_rate between wtp " + str(wtp.addr) +
            #" and lvap " + str(lvap_addr) +
            #" is " + str(self.dl_est_rate[wtp.addr,lvap_addr][0]))
            if len(self.dl_rssi[wtp.addr,lvap_addr]) > self.sliding_window_samples :
                del self.dl_rssi[wtp.addr,lvap_addr][self.sliding_window_samples:]
                del self.dl_est_rate[wtp.addr,lvap_addr][self.sliding_window_samples:]


    def counters_callback(self, stats) :
        """ New stats available. """
    #    self.log.info("New counters received from %s" % stats.lvap)
        self.bincounter_stats_counter += 1
        ## fix
        lvap_addr = stats.lvap
        #wtp = stats.lvap.wtp
     #   self.log.info("windNum: " + str(self.global_window_counter) +
     #       " bin counter stats recv from lvap: " + str(lvap_addr)+ str(time.time()))

        # For each frame length I have a count. 
        # I am just going to add them all up  and average
        # The counters here seem to be upcounters.
        arr_pps = 0
        this_window_bytes = 0
        this_window_pkts = 0
        if lvap_addr in self.last_counters_stats :
            this_window_bytes = sum(stats.tx_bytes) - sum(self.last_counters_stats[lvap_addr].tx_bytes)
            this_window_pkts = sum(stats.tx_packets) - sum(self.last_counters_stats[lvap_addr].tx_packets)

            if this_window_bytes < 0:
                print(
                    "----------------------PROBLEM----------------frame len negative")
            if this_window_pkts < 0:
                print(
                    "----------------------PROBLEM----------------arr_rate negative")

            arr_pps =  float((this_window_pkts))*1000.0/self.window_time# pps
            if this_window_pkts == 0 :
                avg_frame_len_bytes = 0
            else :    
                avg_frame_len_bytes = float((this_window_bytes))/this_window_pkts


        else :
            arr_pps = sum(stats.tx_packets)*1000.0/self.window_time# pps
            if sum(stats.tx_packets) == 0 :
                avg_frame_len_bytes = 0
            else :    
                avg_frame_len_bytes = float(sum(stats.tx_bytes))/sum(stats.tx_packets)

        if arr_pps == 0 and lvap_addr == EtherAddress(self.tagged_sta_mac_addr):
            print("------------ERROR----------- arrival rate is 0")

        self.last_counters_stats[lvap_addr] = copy.copy(stats)
        
        if lvap_addr not in self.dl_arr_rate_pps :
            self.dl_arr_rate_pps[lvap_addr] = []
            
        self.dl_arr_rate_pps[lvap_addr].insert(0,arr_pps)


        self.log.info("windNum: " + str(self.global_window_counter) +
            " arr_rate_pps " +
            " at lvap " + str(lvap_addr) +
            " is " + str(self.dl_arr_rate_pps[lvap_addr][0]))            

        if lvap_addr not in self.dl_frame_len_bytes :
            self.dl_frame_len_bytes[lvap_addr] = []
        #- table.ETH_HEADER_BYTES I am doing this at the agent itself
        self.dl_frame_len_bytes[lvap_addr].insert(0,avg_frame_len_bytes)
        self.log.info("windNum: " + str(self.global_window_counter) +
            " frame_len_bytes " +
            " at lvap " + str(lvap_addr) +
            " is " + str(self.dl_frame_len_bytes[lvap_addr][0])) 

        if len(self.dl_arr_rate_pps[lvap_addr]) > self.sliding_window_samples :
            del self.dl_arr_rate_pps[lvap_addr][self.sliding_window_samples:]
            del self.dl_frame_len_bytes[lvap_addr][self.sliding_window_samples:]


    def nif_stats_callback(self, nif):
        # This function is called periodically once for each lvap.
        # aggregate data here 
        self.nif_stats_counter += 1
        ## fix
        lvap_addr = nif.lvap
        wtp = RUNTIME.lvaps[lvap_addr].wtp
    #    self.log.info("windNum: " + str(self.global_window_counter) +
    #        " nif stats recv from lvap: " + str(lvap_addr)+ str(time.time()))

        if (lvap_addr == EtherAddress(self.tagged_sta_mac_addr)) :
            self.tagged_lvap_sample_counter += 1

        # hack. this only handles two ues . Need to change this.
        else:
            self.other_lvap_sample_counter += 1

        succ = 0
        att = 0
        acked_bytes = 0
        
        for rate in nif.rates : 
            succ += nif.rates[rate]['hist_successes']
            att += nif.rates[rate]['hist_attempts']
            acked_bytes += nif.rates[rate]['hist_acked_bytes']

        if lvap_addr in self.last_succ:
            tmp_succ = succ - self.last_succ[lvap_addr]
            tmp_att = att - self.last_att[lvap_addr]
            tmp_acked_bytes = acked_bytes - self.last_acked_bytes[lvap_addr]
        else:
            tmp_succ = succ
            tmp_att = att
            tmp_acked_bytes = acked_bytes

        if tmp_att == 0:
            pdr = 0
        else:
            pdr = float(tmp_succ) / tmp_att

        meas_thput_kbps = float(tmp_acked_bytes*8) / self.window_time
        
        if (wtp.addr, lvap_addr) not in self.dl_pdr :
            self.dl_pdr[wtp.addr, lvap_addr] = []

        self.dl_pdr[wtp.addr, lvap_addr].insert(0,pdr)
        self.log.info("windNum: " + str(self.global_window_counter) +
            " pdr between wtp " + str(wtp.addr) + 
            " and lvap " + str(lvap_addr) +
            " is " + str(self.dl_pdr[wtp.addr, lvap_addr][0])) 
        
        if (wtp.addr, lvap_addr) not in self.dl_meas_thput :
            self.dl_meas_thput[wtp.addr, lvap_addr] = []

        self.dl_meas_thput[wtp.addr, lvap_addr].insert(0,meas_thput_kbps)
        self.log.info("windNum: " + str(self.global_window_counter) +
            " meas_thput between " + str(wtp.addr) +
            " and lvap " + str(lvap_addr) +
            " is " + str(self.dl_meas_thput[wtp.addr, lvap_addr][0]))

        self.dl_aggr_attempts[wtp.addr][0] += tmp_att 
        self.dl_aggr_succ[wtp.addr][0] += tmp_succ
        
        rate_with_max_attempts = 0
        max_att = 0
        if lvap_addr in self.last_nif_stats:
            for rate in nif.rates :
                if rate in self.last_nif_stats[lvap_addr].rates :
                    num_att = nif.rates[rate]['hist_attempts'] - self.last_nif_stats[lvap_addr].rates[rate]['hist_attempts']
                else :
                    num_att = nif.rates[rate]['hist_attempts']
                
                if num_att > max_att :
                    max_att = num_att
                    rate_with_max_attempts = rate
        else:
            for rate in nif.rates:
                num_att = nif.rates[rate]['hist_attempts']

                if num_att > max_att:
                    max_att = num_att
                    rate_with_max_attempts = rate


        self.last_succ[lvap_addr] = succ
        self.last_att[lvap_addr] = att
        self.last_acked_bytes[lvap_addr] = acked_bytes
        self.last_nif_stats[lvap_addr] = copy.copy(nif)

        if (wtp.addr, lvap_addr) not in self.dl_meas_rate:
            self.dl_meas_rate[wtp.addr, lvap_addr] = []

        self.dl_meas_rate[wtp.addr, lvap_addr].insert(0,rate_with_max_attempts)
        self.log.info("windNum: " + str(self.global_window_counter) +
            " meas_rate between" + str(wtp.addr) + 
            " and lvap " + str(lvap_addr) +
            " is " + str(self.dl_meas_rate[wtp.addr, lvap_addr][0]))

        if len(self.dl_pdr[wtp.addr, lvap_addr]) > self.sliding_window_samples :
            del self.dl_pdr[wtp.addr,lvap_addr][self.sliding_window_samples:]
            del self.dl_meas_rate[wtp.addr, lvap_addr][self.sliding_window_samples:]
            del self.dl_meas_thput[wtp.addr, lvap_addr][self.sliding_window_samples:]

    def wifi_stats_callback(self, stats):
        return
        #self.log.info("windNum: " + str(self.global_window_counter) +
        #    " wifi stats recv from wtp: " + str(stats.wtp.addr))



    # Evaluate for one wtp association set
    def nif_evaluate_stats(self, wtp_addr, wtp_assoc_set, wtp,block) :

        self.dl_num_active_clients[wtp_addr] = [0]*self.sliding_window_samples
        # All lvaps associated with that wtp
        this_wtps_lvaps = self.lvaps(block=block)

        for lvap in wtp_assoc_set:
            self.dl_att_thput[wtp_addr,lvap.addr] = [0]*self.sliding_window_samples

        for w in range(0,self.sliding_window_samples) :
            self.dl_active_clients = []
            # Find the number of active stations in this window.
            for lvap in wtp_assoc_set :
                if len(self.dl_arr_rate_pps[lvap.addr]) < self.sliding_window_samples:
                    print(
                        "-------------------evaluate called without a full window------------")
                if self.dl_arr_rate_pps[lvap.addr][w] > 0.0 :
                    self.dl_active_clients.append(lvap.addr)
                    self.dl_num_active_clients[wtp_addr][w] += 1

            denominator = 0
            #for wtp_addr in  wtp_assoc_set :
            if self.dl_aggr_attempts[wtp_addr][w] == 0 :
                # If not packets were sent out then this channel is free so pdr (packet delivery ratio) is set to 1.0
                self.dl_aggr_pdr[wtp_addr][w] = 1.0
            else :
                self.dl_aggr_pdr[wtp_addr][w] = float(self.dl_aggr_succ[wtp_addr][w])/self.dl_aggr_attempts[wtp_addr][w]
            for lvap_addr in self.dl_active_clients :

                ack_time = table.ack_time(self.dl_est_rate[wtp_addr,lvap_addr][w])
                denominator += ( (self.dl_arr_rate_pps[lvap_addr][w]) \
                                * (self.dl_frame_len_bytes[lvap_addr][w])) \
                                    /(self.dl_est_rate[wtp_addr,lvap_addr][w] + table.WIFI_DIFS
                                        + table.WIFI_SIFS \
                                        + (float(table.WIFI_MAC_HEADER_BYTES*8*1000)/self.dl_est_rate[wtp_addr,lvap_addr][w])
                                        + table.WIFI_PLCP_HEADER_PREAMBLE_TIME \
                                        + table.ack_time(self.dl_est_rate[wtp_addr,lvap_addr][w]))
        
            # Get stats from the first structure object which is the ue
            # whose attainable throughput is to be measured
            # If arrival rate is zero. i.e. it is not an active client then these eq. will give troughput = 0
            for lvap in wtp_assoc_set :
               thput_unsat = ( (self.dl_arr_rate_pps[lvap.addr][w]) \
                                * (self.dl_frame_len_bytes[lvap.addr][w]) \
                              * self.dl_aggr_pdr[wtp_addr][w] * 8.0) / 1000.0
               if denominator > 0:
                    thput_sat = ( (self.dl_arr_rate_pps[lvap.addr][w]) \
                            * (self.dl_frame_len_bytes[lvap.addr][w]) \
                            * self.dl_aggr_pdr[wtp_addr][w] * 8.0 * 1000.0) / denominator

                    self.dl_att_thput[wtp_addr,lvap.addr][w] = min(thput_sat, thput_unsat)
               else:
                    self.dl_att_thput[wtp_addr,lvap.addr][w] = thput_unsat


    def loop(self):
        """ Periodic job. """
        self.global_window_counter += 1
        self.log.info("windNum: "+ str(self.global_window_counter) +
            " loop timer fired" + str(time.time()))
        # Add callbacks for the new WTPs and LVAPs that 
        # have joined the network since last loop periodic trigger
        self.wtp_up_initialize()
        # reset this list
        self.new_wtps=[]
        self.lvap_join_initialize()        
        # reset this list
        self.new_lvaps=[]
        wtp_assoc_set=[]
        # find the lvap using sta mac addr. ??
        ## fix
        ## corrected
        # This is a dictionary of all the lvaps currently in the network.
        all_lvaps = list(self.lvaps())
        # This is the EtherAddress object for the specified mac address.
        tagged_lvap_etherAddr_obj = EtherAddress(self.tagged_sta_mac_addr)
        # Proceed further only if the lvap I am interested in following has joined the network

        tagged_lvap = None
        for lvap in all_lvaps:
            if lvap.addr == tagged_lvap_etherAddr_obj:
                tagged_lvap = lvap

        if tagged_lvap is None:
            self.log.info("windNum: " + str(self.global_window_counter) +
                          " waiting for tagged sta to come up")
        else:
            self.log.info("windNum: " + str(self.global_window_counter) +
                " tagged sta associated")
            # This is the EtherAddress object for the specified mac address.
            tagged_lvap_curr_assoc_wtp = tagged_lvap.wtp
            best_target_wtp = tagged_lvap_curr_assoc_wtp
            self.log.info("windNum: " + str(self.global_window_counter) +
                " sample counter for tagged sta: " + str(self.tagged_lvap_sample_counter))
            if self.tagged_lvap_sample_counter >= self.sliding_window_samples and self.other_lvap_sample_counter >= self.sliding_window_samples:
                self.log.info("windNum: " + str(self.global_window_counter) +
                    " sample counter for tagged sta >= " + str(self.sliding_window_samples))
                self.dl_meas_prob_good_thput[tagged_lvap_curr_assoc_wtp.addr,tagged_lvap.addr] = \
                                        (sum(i >= self.thput_threshold \
                                            for i in self.dl_meas_thput[tagged_lvap_curr_assoc_wtp.addr,tagged_lvap.addr])
                                            /float(self.sliding_window_samples))
                self.log.info("windNum: " + str(self.global_window_counter) +
                    " P(meas_thput >= " + str(self.thput_threshold) + ") = " + str(self.dl_meas_prob_good_thput[tagged_lvap_curr_assoc_wtp.addr,tagged_lvap.addr]))
                if self.dl_meas_prob_good_thput[tagged_lvap_curr_assoc_wtp.addr,tagged_lvap.addr] < self.tolerance_prob :
                    self.log.info("windNum: " + str(self.global_window_counter) +
                        " tolerance level crossed P(meas_thput >= " + str(self.thput_threshold) + ") is < " + str(self.tolerance_prob))
                    association_changed_flag = False
                    max_prob_satisfying_qos = self.dl_meas_prob_good_thput[tagged_lvap_curr_assoc_wtp.addr,tagged_lvap.addr]
                    for wtp in self.wtps() :

                        if wtp.state == 'disconnected':
                            continue

                        # If it is not then trigger the task of finding a new one,
                        # by iterating through all the association options.
                        # pick the one that is best after iterating through all of them.
                        if wtp.addr != tagged_lvap_curr_assoc_wtp.addr :
                            # Evaluate attainable throughput if tagged sta is moved ot this wtp
                            # All lvaps associated with that wtp
                            for block in wtp.supports:
                                if block.channel != tagged_lvap.blocks[0].channel:
                                    # I am assuming that of all the blocks only 1 block has lvaps on it.
                                    # the others will return NOne for lvaps
                                    #if self.lvaps(block=block) is not None:
                                    # This is the block on which lvaps are scheduled
                                    wtp_assoc_set = copy.copy(self.lvaps(block=block))
                                    wtp_assoc_set.append(tagged_lvap)
                                    self.log.info("windNum: " + str(self.global_window_counter) +
                                    " evaluating tagged sta assoc with wtp: " + str(wtp.addr))
                                    self.nif_evaluate_stats(wtp.addr, wtp_assoc_set,wtp,block)

 #                           prob_satisfying_qos = \
 #                                       (sum(i >= self.thput_threshold \
 #                                           for i in self.dl_att_thput[wtp.addr,tagged_lvap.addr])
 #                                           /float(self.sliding_window_samples))
                            prob_satisfying_qos = 1.0
  #                          self.log.info("windNum: " + str(self.global_window_counter) +
  #                                        "att_thput window is" + str(self.dl_att_thput[wtp.addr,tagged_lvap.addr]))
                            self.log.info("windNum: " + str(self.global_window_counter) +
                                " P(att_thput >= " + str(self.thput_threshold) + ")=" + str(prob_satisfying_qos))
                            if prob_satisfying_qos > max_prob_satisfying_qos :
                                # After this evaluation I need to see if this association set is a fit for the tagged sta.
                                max_prob_satisfying_qos = prob_satisfying_qos
                                best_target_wtp = wtp
                                association_changed_flag = True
                                self.log.info("windNum: " + str(self.global_window_counter) +
                                    "target wtp: " + str(wtp.addr) + " is better than current wtp: " +
                                    str(tagged_lvap.wtp.addr))

                    # I shall now use this wtp with the least prob of violating
                    # qos and associate the tagged lvap with this wtp.

                    # This is supposed to trigger the handover.
                    if association_changed_flag :
                        #tagged_lvap.wtp = best_target_wtp
                        for wtp_block in best_target_wtp.supports:
                            if wtp_block.channel != tagged_lvap.blocks[0].channel:
                                print(">>>>>>>>>>>>>>>>>>...")
                                print(tagged_lvap)
                                print(tagged_lvap.blocks)
                                print(wtp_block)
                                print(">>>>>>>>>>>>>>>>>>>>>>>")
                                self.log.info("windNum: " + str(
                                self.global_window_counter) +
                                          "handover from previous wtp " + str(
                                tagged_lvap_curr_assoc_wtp.addr) + " to target wtp: " + str(
                                best_target_wtp.addr) + "on channel " + str(
                                wtp_block.channel))
                                tagged_lvap.blocks = wtp_block

                                all_lvaps = list(self.lvaps())
                                # This is the EtherAddress object for the specified mac address.
                                tagged_lvap_etherAddr_obj = EtherAddress(
                                    self.tagged_sta_mac_addr)
                                # Proceed further only if the lvap I am interested in following has joined the network

                                tagged_lvap = None
                                for lvap in all_lvaps:
                                    if lvap.addr == tagged_lvap_etherAddr_obj:
                                        tagged_lvap = lvap


                                self.log.info("windNum: " + str(
                                    self.global_window_counter) +
                                              "DONE: handover from previous wtp " + str(
                                    tagged_lvap_curr_assoc_wtp.addr) + " to target wtp: " + str(
                                    tagged_lvap.wtp.addr) + "on channel " + str(
                                    tagged_lvap.blocks[0].channel))
                                break

                        # Reset counters and the measured throughput window since these values cannot be used anymore.
                        self.tagged_lvap_sample_counter = 0
                        self.dl_meas_thput[tagged_lvap_curr_assoc_wtp.addr,tagged_lvap.addr]=[]



            # Reset the things I need to after each loop or each Wm
            for wtp in self.wtps():

                if wtp.state == 'disconnected':
                    continue

                #if wtp.addr not in self.dl_aggr_attempts:
                #    self.dl_aggr_attempts[wtp.addr] = []

                n_attempts = self.dl_aggr_attempts[wtp.addr][0]

                if n_attempts > 0:
                    self.log.info("windNum: " + str(self.global_window_counter) +
                        " aggr_pdr at wtp " + str(wtp.addr) +
                        " is " + str(float(self.dl_aggr_succ[wtp.addr][0])/self.dl_aggr_attempts[wtp.addr][0]))
                else:
                    self.log.info("windNum: " + str(self.global_window_counter) +
                                  " aggr_pdr at wtp " + str(wtp.addr) +
                                  " is 0")

                self.dl_aggr_attempts[wtp.addr] = self.dl_aggr_attempts[wtp.addr][1::]
                self.dl_aggr_succ[wtp.addr] = self.dl_aggr_succ[wtp.addr][1::]
                self.dl_aggr_attempts[wtp.addr].insert(0,0)
                self.dl_aggr_succ[wtp.addr].insert(0,0)


def launch(tenant_id, every=DEFAULT_PERIOD):
    """ Initialize the module. """
    #self.log.info("windNum: ",self.global_window_counter, " starting aquamet")
    return AquametMobilityManager(tenant_id=tenant_id, every=500)
