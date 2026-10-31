//+------------------------------------------------------------------+
//| ExportBars.mq5                                                   |
//| Exports M1 bars for the listed symbols to CSV files in the       |
//| terminal's COMMON data folder:                                   |
//|   %APPDATA%\MetaQuotes\Terminal\Common\Files\bars_<symbol>.csv   |
//| Times are written in BROKER SERVER time, the same clock used in  |
//| the terminal's trade reports, so no timezone conversion is needed|
//| to match them with trades.                                       |
//|                                                                  |
//| The script only READS price history. It places no orders and     |
//| changes nothing on the account.                                  |
//|                                                                  |
//| How to use: save into MQL5\Scripts, open it in MetaEditor, press |
//| F7 (Compile), then drag "ExportBars" from the Navigator onto any |
//| chart. Look at the "Experts" tab for the result of each symbol.  |
//+------------------------------------------------------------------+
#property script_show_inputs
#property strict

input string   InpSymbols = "XAUUSDrfd,EURUSDrfd,USDCHFrfd,GBPUSDrfd,#NDX,#AVGO,#MSTR,#GOOGL,#AMZN,#LCO"; // comma-separated, exactly as in Market Watch
input datetime InpFrom    = D'2026.07.25 00:00';                       // start of export
input datetime InpTo      = 0;                                         // 0 = now
input bool     InpCleanOld = true;                                         // delete old bars_*.csv before exporting

// Deletes ONLY files named bars_*.csv in the common Files folder, so that
// after every run just the fresh export is left. Nothing else is touched.
void CleanOldExports()
{
   string names[];
   string found;
   long   h = FileFindFirst("bars_*.csv", found, FILE_COMMON);
   if(h == INVALID_HANDLE)
      return;                      // nothing to delete
   do
   {
      const int k = ArraySize(names);
      ArrayResize(names, k + 1);
      names[k] = found;
   }
   while(FileFindNext(h, found));
   FileFindClose(h);                // close the search BEFORE deleting

   for(int i = 0; i < ArraySize(names); i++)
   {
      if(FileDelete(names[i], FILE_COMMON))
         PrintFormat("ExportBars: deleted old file %s", names[i]);
      else
         PrintFormat("ExportBars: could not delete %s (error %d)", names[i], GetLastError());
   }
}

void ExportSymbol(const string sym, const datetime from, const datetime to)
{
   if(!SymbolSelect(sym, true))
   {
      PrintFormat("ExportBars: symbol '%s' not found in Market Watch / not available", sym);
      return;
   }

   MqlRates rates[];
   int got = -1;
   // The terminal may need a few seconds to download missing history.
   for(int attempt = 0; attempt < 15; attempt++)
   {
      ResetLastError();
      got = CopyRates(sym, PERIOD_M1, from, to, rates);
      if(got > 0)
         break;
      Sleep(1000);
   }
   if(got <= 0)
   {
      PrintFormat("ExportBars: no M1 data for %s (error %d)", sym, GetLastError());
      return;
   }

   const string fname = "bars_" + sym + ".csv";
   int h = FileOpen(fname, FILE_WRITE | FILE_CSV | FILE_ANSI | FILE_COMMON, ',');
   if(h == INVALID_HANDLE)
   {
      PrintFormat("ExportBars: cannot open %s for writing (error %d)", fname, GetLastError());
      return;
   }

   const int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   FileWrite(h, "time", "open", "high", "low", "close");
   for(int k = 0; k < got; k++)
   {
      FileWrite(h,
                TimeToString(rates[k].time, TIME_DATE | TIME_SECONDS),
                DoubleToString(rates[k].open,  digits),
                DoubleToString(rates[k].high,  digits),
                DoubleToString(rates[k].low,   digits),
                DoubleToString(rates[k].close, digits));
   }
   FileClose(h);

   PrintFormat("ExportBars: %s -> %d bars, %s ... %s, file %s",
               sym, got,
               TimeToString(rates[0].time, TIME_DATE | TIME_MINUTES),
               TimeToString(rates[got - 1].time, TIME_DATE | TIME_MINUTES),
               fname);
}

void OnStart()
{
   const datetime to = (InpTo == 0) ? TimeCurrent() : InpTo;

   if(InpCleanOld)
      CleanOldExports();

   string syms[];
   const int n = StringSplit(InpSymbols, ',', syms);
   for(int i = 0; i < n; i++)
   {
      string s = syms[i];
      StringTrimLeft(s);
      StringTrimRight(s);
      if(s == "")
         continue;
      ExportSymbol(s, InpFrom, to);
   }
   Print("ExportBars: finished");
}
//+------------------------------------------------------------------+
