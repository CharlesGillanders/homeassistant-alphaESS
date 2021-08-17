from os import stat
from alphaess_api import AlphaESSAPI
import json
import time

AlphaESS = AlphaESSAPI("www.alphaess.com", "charlesgillanders", "iNwkjCVdzfL6LiYFwC7")

# print (AlphaESS._host)

if AlphaESS.connect():
    print("AlphaESS connected")
    
   
    for unit in AlphaESS.ess_list():
        if  "sys_sn" in unit:
           serial = unit["sys_sn"] 
           print (serial)
           while True:
               ppv = 0
               soc = 0
               pbat = 0
               load = 0
               grid = 0
               statistics = AlphaESS.GetSecondDataBySn(serial)
               if "ppv1" in statistics:
                   if statistics["ppv1"] is not None:
                       ppv = ppv + statistics["ppv1"]
                       load = load + statistics["ppv1"]
               if "ppv2" in statistics:
                   if statistics["ppv2"] is not None:
                       ppv = ppv + statistics["ppv2"]
                       load = load + statistics["ppv2"]
               if "ppv3" in statistics:
                   if statistics["ppv3"] is not None:
                       ppv = ppv + statistics["ppv3"]
                       load = load + statistics["ppv3"]
               if "soc" in statistics:
                   if statistics["soc"] is not None:
                       soc = statistics["soc"]
               if "pbat" in statistics:
                   if statistics["pbat"] is not None:
                       pbat = statistics["pbat"]
                       load = load + statistics["pbat"]
               if "pmeter_l1" in statistics:
                   if statistics["pmeter_l1"] is not None:
                       grid = grid + statistics["pmeter_l1"]
                       load = load + statistics["pmeter_l1"]
               if "pmeter_l2" in statistics:
                   if statistics["pmeter_l2"] is not None:
                       grid = grid + statistics["pmeter_l2"]
                       load = load + statistics["pmeter_l2"]
               if "pmeter_l3" in statistics:
                   if statistics["pmeter_l3"] is not None:
                       grid = grid + statistics["pmeter_l3"]
                       load = load + statistics["pmeter_l3"]
               print (f"power pv : {ppv} W, State of Charge : {soc} %, Battery : {pbat} W, Load :  {load} W, Grid : {grid} W")

               time.sleep(10)


    if AlphaESS.disconnect():
       print("AlphaESS disconnected")
