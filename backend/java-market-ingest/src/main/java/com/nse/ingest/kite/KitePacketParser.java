package com.nse.ingest.kite;

import com.nse.ingest.dto.TickDto;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.ArrayList;
import java.util.List;

/**
 * Parses Kite Connect binary WebSocket frames into {@link TickDto} objects.
 *
 * Binary frame layout:
 *  [0-1]  : int16  – number of packets
 *  [2-3]  : int16  – first packet length (repeats for each packet)
 *  Packet layout (quote mode = 44 bytes, full mode = 184 bytes):
 *    [0-3]  instrument token (int32)
 *    [4]    tradable flag
 *    [5]    mode flag (ltp=1, quote=2, full=3)
 *    [6-9]  LTP * 100  (int32)
 *    [10-13] last traded qty (int32)
 *    [14-17] avg traded price * 100 (int32)
 *    [18-21] volume (int32)
 *    [22-25] total buy qty (int32)
 *    [26-29] total sell qty (int32)
 *    [30-33] open * 100
 *    [34-37] high * 100
 *    [38-41] low  * 100
 *    [42-45] close * 100
 *   (full mode continues at [46+])
 *    [46-49] last trade time (int32 epoch)
 *    [50-53] OI (int32)
 *    [54-57] OI day high (int32)
 *    [58-61] OI day low  (int32)
 *    [62-65] exchange timestamp (int32 epoch)
 *    Depth: 10 levels * 12 bytes each starting at [66]
 *      Each level: qty(4) + price(4) + orders(2) + padding(2) = 12 bytes
 *      First 5 = bids, next 5 = asks
 */
public class KitePacketParser {

    private final InstrumentTokenRegistry tokenRegistry;

    public KitePacketParser(InstrumentTokenRegistry tokenRegistry) {
        this.tokenRegistry = tokenRegistry;
    }

    public List<TickDto> parse(byte[] data) {
        List<TickDto> ticks = new ArrayList<>();
        if (data == null || data.length < 2) return ticks;

        ByteBuffer buf = ByteBuffer.wrap(data).order(ByteOrder.BIG_ENDIAN);
        int numPackets = buf.getShort() & 0xFFFF;

        for (int i = 0; i < numPackets; i++) {
            if (buf.remaining() < 2) break;
            int pktLen = buf.getShort() & 0xFFFF;
            if (buf.remaining() < pktLen) break;

            byte[] pkt = new byte[pktLen];
            buf.get(pkt);
            TickDto tick = parsePacket(pkt);
            if (tick != null) ticks.add(tick);
        }
        return ticks;
    }

    private TickDto parsePacket(byte[] pkt) {
        if (pkt.length < 8) return null;
        ByteBuffer b = ByteBuffer.wrap(pkt).order(ByteOrder.BIG_ENDIAN);

        // Kite packet layout (no tradable/mode bytes — inferred from token & length):
        // LTP mode (8 bytes):  [0-3] token, [4-7] ltp
        // Quote mode (44 bytes): [0-3] token, [4-7] ltp, [8-11] lastQty, [12-15] avg,
        //   [16-19] vol, [20-23] buyQ, [24-27] sellQ, [28-31] open, [32-35] high,
        //   [36-39] low, [40-43] close
        // Full mode (184 bytes): quote + [44-47] lastTradeTime, [48-51] OI,
        //   [52-55] OI-hi, [56-59] OI-lo, [60-63] exchTs, [64-183] depth(10×12)

        int token = b.getInt();            // [0-3]
        double ltp = b.getInt() / 100.0;  // [4-7]

        long   lastQty    = 0;
        double avgPrice   = 0;
        long   volume     = 0;
        long   totalBuyQ  = 0;
        long   totalSellQ = 0;
        double open  = 0, high = 0, low = 0, close = 0;
        long   ts    = 0;
        double bestBidPrice = Double.NaN, bestAskPrice = Double.NaN;
        long   bestBidQty = 0, bestAskQty = 0;

        if (pkt.length >= 44) {
            lastQty    = b.getInt() & 0xFFFFFFFFL; // [8-11]
            avgPrice   = b.getInt() / 100.0;        // [12-15]
            volume     = b.getInt() & 0xFFFFFFFFL;  // [16-19]
            totalBuyQ  = b.getInt() & 0xFFFFFFFFL;  // [20-23]
            totalSellQ = b.getInt() & 0xFFFFFFFFL;  // [24-27]
            open       = b.getInt() / 100.0;        // [28-31]
            high       = b.getInt() / 100.0;        // [32-35]
            low        = b.getInt() / 100.0;        // [36-39]
            close      = b.getInt() / 100.0;        // [40-43]
        }

        if (pkt.length >= 184) {
            ts = b.getInt() & 0xFFFFFFFFL; // [44-47] last trade time
            b.getInt();                    // [48-51] OI
            b.getInt();                    // [52-55] OI day high
            b.getInt();                    // [56-59] OI day low
            b.getInt();                    // [60-63] exchange ts

            // Depth: 5 bid + 5 ask levels, each 12 bytes, starting at [64]
            long bidQtySum = 0;
            double firstBidPrice = Double.NaN;
            long   firstBidQty  = 0;
            for (int j = 0; j < 5; j++) {
                long   dq = b.getInt() & 0xFFFFFFFFL;
                double dp = b.getInt() / 100.0;
                b.getShort(); // orders
                b.getShort(); // padding
                bidQtySum += dq;
                if (j == 0) { firstBidPrice = dp; firstBidQty = dq; }
            }
            long askQtySum = 0;
            double firstAskPrice = Double.NaN;
            long   firstAskQty  = 0;
            for (int j = 0; j < 5; j++) {
                long   dq = b.getInt() & 0xFFFFFFFFL;
                double dp = b.getInt() / 100.0;
                b.getShort();
                b.getShort();
                askQtySum += dq;
                if (j == 0) { firstAskPrice = dp; firstAskQty = dq; }
            }
            bestBidPrice = firstBidPrice;
            bestBidQty   = firstBidQty;
            bestAskPrice = firstAskPrice;
            bestAskQty   = firstAskQty;
        }

        String symbol = tokenRegistry.resolve(token);
        if (symbol == null) symbol = "TOKEN:" + token;

        return new TickDto(
            token, symbol, ltp, lastQty, avgPrice,
            volume, totalBuyQ, totalSellQ,
            open, high, low, close,
            bestBidPrice, bestBidQty,
            bestAskPrice, bestAskQty,
            ts
        );
    }
}
