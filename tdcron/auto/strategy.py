# -*- coding: utf-8 -*-
"""
Created on Mon Aug 17 18:13:52 2015

@author: guosen
"""
#import trade
#import quote


import quote
from util import *
from eventengine import *

logger = logging.getLogger("run")


class Strategy(object):
    def __init__(self, cf, code, eventEngine_):
        self._code = code
        self._quote = quote.Quote5mKline(cf, code)
        self._name = self._quote._name
        self._eventEngine = eventEngine_

        #订阅合约的行情
        self._eventEngine.register(EVENT_MARKETDATA_CONTRACT + self._code, self.OnTick)
        #定时事件
        self._eventEngine.register(EVENT_TIMER, self.OnTimerCall)

        self._latestStatus = 'init'

    def OnTick(self, event):
        #logger.debug("code:%s OnTick", self._code)
        tick = event.dict_['tick']
        self._quote.OnTick(tick, self.OnNewKLine)

    def OnNewKLine(self, kline):
        try:
            isNeedBuy, isNeedSell = td(kline)
            logger.info("code:%s,isNeedBuy:%s,isNeedSell:%s", self._code, isNeedBuy, isNeedSell)
            if isNeedBuy:
                if self._latestStatus == 'buy':
                    return

                self._latestStatus = 'buy'
                self.DealBuy()


            if isNeedSell:
                if self._latestStatus == 'sell':
                    return

                self._latestStatus = 'sell'
                self.DealSell()
        except BaseException,e:
            logger.exception(e)
            
    def OnTimerCall(self, event):
        pass
        #logger.debug("code:%s OnTimerCall", self._code)
            
    def DealBuy(self):
        pass
    
    def DealSell(self):
        pass


class Stg_Signal(Strategy):
    def __init__(self, cf, code, eventEngine_):
        super(Stg_Signal, self).__init__(cf, code, eventEngine_)

        self.GenToAddrList(cf)
                
    def GenToAddrList(self, cf):
        toaddr_codelist = {}
        reveivers = cf.get("signal", "reveiver")
        reveivers = reveivers.split(',')
        
        for reveiver in reveivers:
            toaddr_codelist[reveiver] = cf.get("signal", reveiver).split(',')
            
        self._to_addr_list = []
        for toaddr,codelist in toaddr_codelist.items():
            if self._code in codelist:
                self._to_addr_list.append(toaddr)
                
        if len(self._to_addr_list) < 1:
            logger.warn("code:%s cant find the to_addr, will send to the from_addr:%s",
                        self._code, cf.get("signal", "from_addr"))
            self._to_addr_list.append(cf.get("signal", "from_addr"))
            
    def SendMail(self, status):
        event = Event(type_=EVENT_SENDMAIL)
        event.dict_['remarks'] = 'Signal'
        event.dict_['content'] = 'code:%s, name:%s, 5min %s' % (self._code, self._name, status)
        event.dict_['to_addr'] = self._to_addr_list
        self._eventEngine.put(event)
        logger.info('sendmail code:%s, 5min %s, to_addr:%s', self._code, status, self._to_addr_list)

    def DealBuy(self):
        self.SendMail('buy')
    
    def DealSell(self):
        self.SendMail('sell')
        
#remark用于区分通过那个交易接口下单
class Stg_Autotrader(Strategy):
    def __init__(self, cf, code, remarks, eventEngine_):
        super(Stg_Autotrader, self).__init__(cf, code, eventEngine_)
        self._remarks = remarks
        self._stock_number = cf.get(self._remarks, self._code)
        self._to_addr_list = cf.get(self._remarks, "reveiver").strip().split(',')
        #self.GenToAddrList(cf)

        #订阅合约的成交
        self._eventEngine.register(EVENT_MATCH_CONTRACT + self._code, self.onMatch)
        #控制当天买入不能卖出
        self._todayHaveBuy = False
        self._bNeedToSellAtOpen = False
        self._sellTime = datetime.datetime.strptime(cf.get("autotrader", "selltime"), "%H:%M").time()

        self._iBuyIndex, self._iSellIndex = self.LookBackRealBuySellPoint()
        
        self._lastBuyPoint = self._quote._df5mKline.index[self._iBuyIndex-1]
        self._lastSellPoint = self._quote._df5mKline.index[self._iSellIndex-1]
        logger.info("code:%s, lastBuyPoint:%s, lastSellPoint:%s", 
                    self._code, self._lastBuyPoint, self._lastSellPoint)
                    
        self.LookBackToGetSignal()
        
        if self.IsNeedToSellAtOpen():
            self._bNeedToSellAtOpen = True
            logger.info("code:%s NeedToSellAtOpen sellTime:%s", self._code, self._sellTime)

        #买卖不成功时重试
        self._retry = cf.getint("autotrader", "retry")
        #重试计数
        self._curRetryCount = 0
        self._bNeedRetryWhileOrderFailed = self._retry > 0
        self._bOrderOk = True


    def onMatch(self,event_):
        event = Event(type_= EVENT_SENDMAIL)
        event.dict_['remarks'] = 'Autotrader'
        event.dict_['content'] = self._code + ' ' + event_.dict_['ORDER_STATUS'] \
                                 + ' ' + event_.dict_['MATCHED_TYPE'] \
                                 + ' ' + str(event_.dict_['MATCHED_QTY']) \
                                 + ' ' + str(event_.dict_['MATCHED_PRICE'])
        event.dict_['to_addr'] = self._to_addr_list
        self._eventEngine.put(event)
        logger.info("put sendmail event code:%s, remarks:%s, content:%s, to_addr_list:%s",
                    self._code, event.dict_['remarks'], event.dict_['content'], event.dict_['to_addr'])

    #检测需不需要在开盘卖掉（如果最后一个真实的买入信号发生在上一个交易日，并且最后一个是卖出信号）
    def IsNeedToSellAtOpen(self):
        if IsLastTradingDay(self._lastBuyPoint.date()) and self._latestStatus == 'sell':
            return True
        return False
            
    #往前回溯获取上一个有效信号
    def LookBackToGetSignal(self):
        for i in xrange(self._quote._df5mKline.shape[0], 61, -1):
            isNeedBuy, isNeedSell = td(self._quote._df5mKline[:i])
            if isNeedBuy or isNeedSell:
                break
        if isNeedBuy:
            self._latestStatus = 'buy'
        elif isNeedSell:
            self._latestStatus = 'sell'
            
        logger.info("LookBackToGetSignal and set the _latestStatus to %s", self._latestStatus)
        
            
    def LookBackRealBuySellPoint(self):
        iBuyIndex = 0
        iSellIndex = 0
        isHaveFoundBuy = False
        isHaveFoundSell = False
        lastHitStatus = 'init'
        
        for i in xrange(self._quote._df5mKline.shape[0], 61, -1):
            isNeedBuy, isNeedSell = td(self._quote._df5mKline[:i])
            if isNeedBuy or isNeedSell:
                if isNeedBuy:
                    if lastHitStatus == 'sell':
                        isHaveFoundSell = True
                    
                    lastHitStatus = 'buy'
                    if not isHaveFoundBuy:
                        iBuyIndex = i
                    
                if isNeedSell:
                    if lastHitStatus == 'buy':
                        isHaveFoundBuy = True
                        
                    lastHitStatus = 'sell'
                    if not isHaveFoundSell:
                        iSellIndex = i
                    
            if isHaveFoundBuy and isHaveFoundSell:
                break
            
        return iBuyIndex,iSellIndex
            
    def OnTimerCall(self, event):
        try:
            if self._bNeedToSellAtOpen and self._latestStatus == 'sell' and datetime.datetime.now().time() > self._sellTime:
                logger.info("deal the sell at open issue")
                self.DealSell()
                self._bNeedToSellAtOpen = False

            if self._bNeedRetryWhileOrderFailed and not self._bOrderOk and self._curRetryCount < self._retry - 1:
                self._curRetryCount += 1
                if self._latestStatus == 'buy':
                    self.DealBuy()
                elif self._latestStatus == 'sell':
                    self.DealSell()

                #重试成功之后，将计数器重置为0，以便下次重试
                if self._bOrderOk:
                    self._curRetryCount = 0
                else:
                    msg = "failed to retry %s:%s %s with number:%s"%(self._latestStatus, self._code, self._name, self._stock_number)
                    logger.warn(msg)
        except BaseException,e:
                msg = "occur exception when %s:%s %s with number:%s"%(self._latestStatus, self._code, self._name, self._stock_number)
                logger.warn(msg)

    def sendOrder(self, direction):
        event = Event(type_= EVENT_TRADE_REMARKS + self._remarks)
        event.dict_['direction'] = direction
        event.dict_['code'] = self._code
        event.dict_['number'] = self._stock_number
        self._eventEngine.put(event)

        event = Event(type_=EVENT_SENDMAIL)
        event.dict_['remarks'] = 'AutoTrade'
        event.dict_['content'] = 'code:%s, name:%s, 5min %s' % (self._code, self._name, direction)
        event.dict_['to_addr'] = self._to_addr_list
        self._eventEngine.put(event)
        
    def DealBuy(self):
        if not self._todayHaveBuy:
            self._todayHaveBuy = True
            self.sendOrder("buy")

    def DealSell(self):
        #控制当天买入当天不能卖出
        if self._todayHaveBuy:
            msg = "code:%s today have buy, so cant sell today" % self._code
            logger.warn(msg)
            event = Event(type_=EVENT_SENDMAIL)
            event.dict_['remarks'] = 'AutoTrade'
            event.dict_['content'] = msg
            event.dict_['to_addr'] = self._to_addr_list
            self._eventEngine.put(event)
            return

        self.sendOrder("sell")
