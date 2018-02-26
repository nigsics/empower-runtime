import math

RECV_NOISE = -95.0
WIFI_STD = "g20mhz"

# Std 802.11a/g 20 MHz
g_mcs_table = [-1, -1, 0, 0, 1, 2, 2, 2, 2, 3, 3, 
	4, 4, 4, 4, 5, 5, 5, 6, 6, 7, 
	7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
	7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
	7, 7, 7, 7, 7, 7, 7, 7, 7, 7]

# std 802.11n 20 MHz and 40 MHz
n_mcs_table = [
	# 20 MHz
	[-1, -1, 0, 0, 0, 1, 1, 1, 1, 2, 2,
	3, 3, 3, 3, 4, 4, 4, 5, 5, 6,
	6, 6, 6, 6, 7, 7, 7, 7, 7, 7,
	7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 
	7, 7, 7, 7, 7, 7, 7, 7, 7, 7],
	# 40 MHz
	[-1, -1, -1, -1, -1, 0, 0, 0, 1, 1, 1,
	1, 2, 2, 3, 3, 3, 3, 4, 4, 4,
	5, 5, 6, 6, 6, 6, 6, 7, 7, 7,
	7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 
	7, 7, 7, 7, 7, 7, 7, 7, 7, 7]]

g_mcs_coderate_table = [1.0/2.0, 3.0/4.0, 1.0/2.0, 3.0/4.0, 1.0/2.0, 3.0/4.0, 2.0/3.0, 3.0/4.0]

g_mcs_sendingrate_table = [6000, 9000, 12000, 18000, 24000, 36000, 48000, 54000]

def GetNoise() :
	return (RECV_NOISE)

def GetSnrFromRssi (rssi_db) :
	snr_db = rssi_db - GetNoise()
	return (snr_db)

def GetMcsFromSnr(snr_db):
	snr_db_lower = int(math.floor(snr_db)); 
	if WIFI_STD == "g20mhz" :
		return(g_mcs_table[snr_db_lower])
	#
	#elif WIFI_STD == "n20mhz" :
	#	return(n_mcs_table[0][snr_db_lower])
	#elif WIFI_STD == "n40mhz" :
	#	return(n_mcs_table[0][snr_db_lower])
	# ERROR: Unrecognised 802.11 standard
	else : 
		return (-1)

def GetSendingRateFromMcs(mcs) : 
	return(g_mcs_sendingrate_table[mcs])

def GetEstimatedMcsFromRssi (rssi_db) :
	return(GetMcsFromSnr(GetSnrFromRssi(rssi_db)))

def GetEstimatedSendingRateFromRssi(rssi_db) :
	return(GetSendingRateFromMcs(GetMcsFromSnr(GetSnrFromRssi(rssi_db))))


def ack_time (mcs) : 
	rate_ack 
	t_ack
	if GetSendingRateFromMcs(mcs) >= 24000 :
		rate_ack = 24000
	else :
		rate_ack = 2000

	return (((float)(L_ACK * 8 * 1000) / (float)(rate_ack)) + T_HEADER)