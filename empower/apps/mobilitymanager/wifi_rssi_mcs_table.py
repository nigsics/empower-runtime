import math

RECV_NOISE = -98.0
WIFI_STD = 'g20mhz'

# Std 802.11a/g 20 MHz
# The index is snr in dB. starting with index 0 or snr = 0 
g_mcs_table = [-1, -1, 0, 0, 1, 2, 2, 2, 2, 3, 3, 
	4, 4, 4, 4, 5, 5, 5, 6, 6, 7, 
	7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
	7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
	7, 7, 7, 7, 7, 7, 7, 7, 7, 7]

# std 802.11n 20 MHz and 40 MHz
# The index is snr in dB. starting with index 0 or snr = 0
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

# sending rate in Kbps
# index is the mcs number in range 0-7 
g_mcs_sendingrate_table = [6000, 9000, 12000, 18000, 24000, 36000, 48000, 54000]
basic_rate = {}
basic_rate['g20mhz'] = 6000 # Kbps
basic_rate['n20mhz'] = 6000 # Kbps

def GetNoise() :
	return (RECV_NOISE)

def GetSnrFromRssi (rssi_db) :
	snr_db = rssi_db - GetNoise()
	return (snr_db)

def GetMcsFromSnr(snr_db):
	snr_db_lower = int(math.floor(snr_db))
	if (snr_db_lower < 0) :
		print("ERROR: received snr is < 0")
		return(-1) 
	if WIFI_STD == "g20mhz" :
		if snr_db_lower >= len(g_mcs_table) :
			return (g_mcs_table[len(g_mcs_table) -1])

		return(g_mcs_table[snr_db_lower])
	elif WIFI_STD == "n20mhz" :
		return(n_mcs_table[0][snr_db_lower])
	elif WIFI_STD == "n40mhz" :
		return(n_mcs_table[1][snr_db_lower])
	else : 
		print("ERROR: Unhandled 802.11 standard")
		return (-1)

def GetSendingRateFromMcs(mcs) : 
	if mcs < 0 :
		print ("ERROR Cannot get sendingrate for mcs < 0 ")
		return(-1)

	return(g_mcs_sendingrate_table[mcs])

def GetEstimatedMcsFromRssi (rssi_db) :
	return(GetMcsFromSnr(GetSnrFromRssi(rssi_db)))

def GetEstimatedSendingRateFromRssi(rssi_db) :
	return(GetSendingRateFromMcs(GetMcsFromSnr(GetSnrFromRssi(rssi_db))))

# time in micro seconds
def ack_time (mcs) : 
	if GetSendingRateFromMcs(mcs) >= 24000 :
		rate_ack = 24000
	else :
		rate_ack = 2000

	return ((float(ACK_BYTES + WIFI_MAC_HEADER_BYTES * 8 * 1000) / float(rate_ack)) \
		+ WIFI_PLCP_HEADER_PREAMBLE_TIME)