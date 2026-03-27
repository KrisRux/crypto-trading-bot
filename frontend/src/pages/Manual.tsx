import { useLang } from '../hooks/useLang'
import type { Lang } from '../i18n'

/* ------------------------------------------------------------------ */
/*  Manual content — end-user focused (no installation/setup)          */
/* ------------------------------------------------------------------ */

interface Section {
  title: string
  body: string
  subsections?: { title: string; body: string }[]
}

function manual(lang: Lang): { title: string; disclaimer: string; sections: Section[] } {
  if (lang === 'it') {
    return {
      title: 'Manuale Utente',
      disclaimer:
        'Questo software e fornito a scopo educativo e sperimentale. Il trading di criptovalute comporta rischi elevati, inclusa la perdita totale del capitale investito. CryptoBot non costituisce consulenza finanziaria. Utilizza il bot a tuo rischio e pericolo.',
      sections: [
        {
          title: '1. Panoramica',
          body: 'CryptoBot e un bot di trading automatico per criptovalute collegato a Binance. Analizza il mercato in tempo reale, applica strategie di analisi tecnica e piazza ordini in autonomia. Puoi monitorare tutto comodamente da questa interfaccia web, sia da PC che da smartphone.',
          subsections: [
            {
              title: 'Cosa puo fare',
              body: `<ul>
                <li>Operare in modalita <strong>Simulata</strong> (nessun rischio) o <strong>Live</strong> (ordini reali)</li>
                <li>Mostrare prezzi aggiornati in tempo reale</li>
                <li>Applicare strategie configurabili: SMA Crossover, RSI Reversal, MACD Crossover</li>
                <li>Gestire automaticamente Stop Loss e Take Profit su ogni posizione</li>
                <li>Limitare il rischio per singola operazione (position sizing)</li>
                <li>Esportare lo storico dei trade in formato CSV</li>
              </ul>`,
            },
          ],
        },
        {
          title: '2. Modalita Simulata vs Live',
          body: `<p>Ogni utente puo scegliere in quale modalita operare dalla pagina <strong>Impostazioni</strong>. La modalita attiva e indicata nella Dashboard con un badge colorato.</p>
          <table>
            <tr><th>Simulato (Paper)</th><th>Live (Reale)</th></tr>
            <tr><td>Portafoglio virtuale — nessun denaro reale coinvolto</td><td>Ordini reali inviati a Binance con le tue chiavi API</td></tr>
            <tr><td>Usa prezzi di mercato reali per la simulazione</td><td>Opera direttamente sul tuo conto Binance</td></tr>
            <tr><td>Richiede chiavi API Testnet</td><td>Richiede chiavi API Live</td></tr>
            <tr><td>Il portafoglio puo essere resettato</td><td>Le operazioni non sono reversibili</td></tr>
          </table>
          <p>Ogni utente ha il proprio portafoglio e i propri dati completamente separati dagli altri utenti. I dati Paper e Live non si mescolano mai.</p>`,
          subsections: [
            {
              title: 'Attenzione alla modalita Live',
              body: `<p>In modalita Live ogni operazione del bot comporta movimenti di denaro reale sul tuo conto Binance. Assicurati di aver prima testato la configurazione in modalita Simulata per un periodo adeguato.</p>`,
            },
          ],
        },
        {
          title: '3. Dashboard',
          body: '<p>La Dashboard e la schermata principale. Da qui puoi tenere sotto controllo lo stato complessivo del bot e delle tue operazioni.</p>',
          subsections: [
            {
              title: '3.1 Riquadri Statistiche',
              body: `<p>I quattro riquadri in alto mostrano un riassunto immediato:</p>
              <ul>
                <li><strong>Saldo Disponibile</strong> — I USDT liberi, non impegnati in posizioni aperte</li>
                <li><strong>Patrimonio Totale</strong> — Saldo disponibile + valore corrente di tutte le posizioni aperte</li>
                <li><strong>PnL Totale</strong> — Profitto o perdita complessiva dall'inizio. Verde indica profitto, rosso perdita</li>
                <li><strong>Tasso Vittoria</strong> — Percentuale di trade chiusi in profitto, con il conteggio esatto di vittorie e perdite</li>
              </ul>`,
            },
            {
              title: '3.2 Barra di Stato',
              body: `<p>Sotto le statistiche trovi informazioni operative:</p>
              <ul>
                <li><strong>Motore</strong> — Indica se il bot e attivo o fermo</li>
                <li><strong>Simbolo</strong> — La coppia di trading corrente (es. BTCUSDT)</li>
                <li><strong>Ultimo Prezzo</strong> — Il prezzo piu recente ricevuto dal mercato</li>
              </ul>
              <p>In modalita simulata compaiono anche due pulsanti aggiuntivi:</p>
              <ul>
                <li><strong>Resetta Portafoglio</strong> — Riporta il saldo virtuale al valore iniziale, eliminando tutte le posizioni e lo storico. Utile per ricominciare da zero dopo aver modificato le strategie</li>
                <li><strong>Esporta CSV</strong> — Scarica un file con lo storico completo dei trade, apribile con Excel o Google Sheets per analisi piu approfondite</li>
              </ul>`,
            },
            {
              title: '3.3 Posizioni Aperte',
              body: `<p>Tabella con tutte le operazioni attualmente in corso. Per ogni posizione puoi vedere:</p>
              <ul>
                <li><strong>Simbolo</strong> — La coppia di trading</li>
                <li><strong>Quantita</strong> — Quanto e stato acquistato</li>
                <li><strong>Prezzo di Ingresso</strong> — A che prezzo e stata aperta la posizione</li>
                <li><strong>Prezzo Attuale</strong> — Il prezzo corrente di mercato</li>
                <li><strong>PnL</strong> — Il guadagno o la perdita non ancora realizzata</li>
                <li><strong>SL / TP</strong> — I livelli di Stop Loss (in rosso) e Take Profit (in verde) impostati</li>
              </ul>`,
            },
            {
              title: '3.4 Trade Recenti',
              body: `<p>Lo storico delle ultime operazioni, sia aperte che chiuse. Per ogni trade puoi vedere il prezzo di ingresso, quello di uscita (se chiuso), il profitto/perdita realizzato in USDT e in percentuale, e quale strategia lo ha generato.</p>`,
            },
          ],
        },
        {
          title: '4. Strategie e Parametri',
          body: '<p>Nella pagina <strong>Strategie</strong> puoi controllare quali algoritmi il bot utilizza per decidere quando comprare o vendere, e con quali parametri.</p>',
          subsections: [
            {
              title: '4.1 Strategie Disponibili',
              body: `<p>Ogni strategia ha un interruttore <strong>ON/OFF</strong> per attivarla o disattivarla, e dei campi numerici per regolarne il comportamento.</p>
              <ul>
                <li><strong>SMA Crossover</strong> (Incrocio Medie Mobili) — Confronta una media mobile veloce con una lenta. Quando la veloce supera la lenta al rialzo, il bot interpreta il segnale come un'opportunita di acquisto. Al contrario, genera un segnale di vendita.
                  <ul>
                    <li><em>fast_period</em> — Numero di candele per la media veloce (predefinito: 10)</li>
                    <li><em>slow_period</em> — Numero di candele per la media lenta (predefinito: 30)</li>
                  </ul>
                </li>
                <li><strong>RSI Reversal</strong> (Inversione RSI) — L'RSI misura se un asset e "ipercomprato" o "ipervenduto". Quando l'RSI scende sotto la soglia di ipervenduto e poi risale, il bot genera un segnale di acquisto. Quando sale sopra la soglia di ipercomprato e poi scende, genera un segnale di vendita.
                  <ul>
                    <li><em>period</em> — Periodo di calcolo RSI (predefinito: 14)</li>
                    <li><em>oversold</em> — Soglia ipervenduto (predefinito: 30)</li>
                    <li><em>overbought</em> — Soglia ipercomprato (predefinito: 70)</li>
                  </ul>
                </li>
                <li><strong>MACD Crossover</strong> — Il MACD e un indicatore di momentum. Quando la linea MACD incrocia al rialzo la linea di segnale, il bot compra. Al contrario, vende.
                  <ul>
                    <li><em>fast</em> — Periodo EMA veloce (predefinito: 12)</li>
                    <li><em>slow</em> — Periodo EMA lenta (predefinito: 26)</li>
                    <li><em>signal</em> — Periodo linea di segnale (predefinito: 9)</li>
                  </ul>
                </li>
              </ul>
              <p>Puoi modificare i valori direttamente nei campi e il bot li applichera dal ciclo successivo (ogni 60 secondi). Puoi anche tenere piu strategie attive contemporaneamente.</p>`,
            },
            {
              title: '4.2 Gestione del Rischio',
              body: `<p>Sotto le strategie trovi i parametri di gestione del rischio, che controllano quanto il bot puo rischiare per ogni operazione:</p>
              <ul>
                <li><strong>Dimensione Max Posizione (%)</strong> — La percentuale massima del capitale totale che il bot puo investire in una singola operazione. Esempio: con un patrimonio di 10.000 USDT e un limite del 2%, ogni trade sara al massimo di 200 USDT</li>
                <li><strong>Stop Loss (%)</strong> — Di quanto puo scendere il prezzo dal punto di acquisto prima che il bot chiuda automaticamente la posizione per limitare le perdite. Esempio: 3% significa che se il bot compra a 50.000, chiudera a 48.500</li>
                <li><strong>Take Profit (%)</strong> — Di quanto deve salire il prezzo per chiudere la posizione e incassare il profitto. Esempio: 5% significa chiusura automatica a 52.500 se il bot ha comprato a 50.000</li>
              </ul>
              <p>Dopo aver modificato i valori, clicca <strong>Salva Parametri Rischio</strong> per renderli effettivi.</p>`,
            },
          ],
        },
        {
          title: '5. Log ed Esecuzione',
          body: '<p>La pagina <strong>Log</strong> ti permette di vedere in dettaglio cosa sta facendo il bot momento per momento.</p>',
          subsections: [
            {
              title: '5.1 Segnali Recenti',
              body: `<p>Ogni volta che una strategia individua un'opportunita, il segnale viene registrato qui con:</p>
              <ul>
                <li><strong>Ora</strong> — Quando e stato generato</li>
                <li><strong>Tipo</strong> — BUY (acquisto) o SELL (vendita), evidenziato a colori</li>
                <li><strong>Simbolo</strong> — Su quale coppia (es. BTCUSDT)</li>
                <li><strong>Prezzo</strong> — Il prezzo al momento del segnale</li>
                <li><strong>Strategia</strong> — Quale algoritmo lo ha generato</li>
                <li><strong>Motivo</strong> — La spiegazione tecnica (es. "SMA golden cross")</li>
              </ul>
              <p>I dati si aggiornano automaticamente ogni 5 secondi.</p>`,
            },
            {
              title: '5.2 Storico Ordini',
              body: `<p>Qui trovi tutti gli ordini che il bot ha inviato (o simulato), con il loro esito:</p>
              <ul>
                <li><strong>FILLED</strong> (verde) — Ordine eseguito con successo</li>
                <li><strong>PENDING</strong> (giallo) — Ordine in attesa (per ordini limit non ancora eseguiti)</li>
                <li><strong>FAILED</strong> (rosso) — Ordine fallito. Il messaggio di errore e visibile nella colonna "Errore"</li>
              </ul>
              <p>Se un ordine fallisce, il motore registra l'errore e continua a funzionare normalmente.</p>`,
            },
          ],
        },
        {
          title: '6. Come Funziona il Bot',
          body: `<p>Il bot esegue un ciclo automatico ogni 60 secondi:</p>
          <ol>
            <li><strong>Legge il mercato</strong> — Scarica gli ultimi dati di prezzo (candele a 1 minuto) da Binance</li>
            <li><strong>Controlla le posizioni aperte</strong> — Se una posizione ha raggiunto il livello di Stop Loss o Take Profit, la chiude automaticamente</li>
            <li><strong>Analizza con le strategie</strong> — Tutte le strategie attive esaminano i dati e decidono se generare segnali di acquisto o vendita</li>
            <li><strong>Esegue gli ordini</strong> — Se viene generato un segnale, il bot calcola quanto investire (rispettando i limiti di rischio), imposta SL e TP, e piazza l'ordine</li>
          </ol>
          <p>In parallelo, i prezzi vengono aggiornati continuamente in tempo reale tramite connessione WebSocket con Binance.</p>`,
        },
        {
          title: '7. Consigli Pratici',
          body: `<ol>
            <li><strong>Inizia sempre in modalita Simulata</strong> — Prendi confidenza con il bot senza rischiare denaro reale. Osserva come si comporta per almeno qualche giorno</li>
            <li><strong>Controlla i segnali nella pagina Log</strong> — Verifica che le decisioni del bot abbiano senso rispetto al mercato</li>
            <li><strong>Sperimenta con i parametri</strong> — Prova combinazioni diverse di periodi SMA, soglie RSI e percentuali di Stop Loss / Take Profit</li>
            <li><strong>Usa l'export CSV per analizzare i risultati</strong> — Apri il file in un foglio di calcolo per capire quali strategie funzionano meglio</li>
            <li><strong>Passa a Live solo quando sei soddisfatto</strong> — E anche in quel caso, mantieni dimensioni di posizione contenute</li>
            <li><strong>Non investire mai piu di quanto puoi permetterti di perdere</strong> — Nessun bot elimina il rischio di mercato</li>
          </ol>`,
        },
        {
          title: '8. Impostazioni e Sicurezza',
          body: '<p>Nella pagina <strong>Impostazioni</strong> (visibile a utenti admin e user, non ai guest) puoi configurare il tuo account personale.</p>',
          subsections: [
            {
              title: '8.1 Chiavi API Binance',
              body: `<p>Ogni utente inserisce le proprie chiavi API Binance. Le chiavi sono personali e separate da quelle degli altri utenti.</p>
              <ul>
                <li><strong>Testnet</strong> — Per il paper trading. Le ottieni su testnet.binance.vision</li>
                <li><strong>Live</strong> — Per il trading reale. Abilita solo "Spot &amp; Margin Trading", mai "Withdrawals"</li>
              </ul>
              <p>Le chiavi vengono salvate in modo sicuro nel database del server e non sono mai visibili per intero dopo il salvataggio.</p>`,
            },
            {
              title: '8.2 Modalita Trading',
              body: `<p>Ogni utente puo scegliere indipendentemente se operare in modalita <strong>Simulata</strong> o <strong>Live</strong>. Puoi stare in Paper mentre un altro utente e in Live.</p>`,
            },
            {
              title: '8.3 Capitale Iniziale',
              body: `<p>Puoi configurare il capitale iniziale del tuo portafoglio virtuale (paper trading). Per applicare il nuovo valore, resetta il portafoglio dalla Dashboard.</p>`,
            },
            {
              title: '8.4 Orario di Trading',
              body: `<p>Puoi configurare una finestra oraria in cui il bot opera (es. dalle 08:00 alle 22:00 UTC). Fuori da questa finestra il bot continua a monitorare le posizioni aperte per Stop Loss e Take Profit, ma non apre nuove posizioni. Lascia i campi vuoti per operare 24/7.</p>
              <p>Supporta anche fasce notturne (es. 22:00 - 08:00). Gli orari sono in formato UTC.</p>`,
            },
            {
              title: '8.5 Logout Automatico',
              body: `<p>Per sicurezza, il sistema ti disconnette automaticamente dopo un periodo di inattivita (predefinito: 30 minuti). L'inattivita viene misurata in base a mouse, tastiera e touch. Al ritorno vedrai la pagina di login.</p>`,
            },
          ],
        },
        {
          title: '9. Domande Frequenti',
          body: '',
          subsections: [
            {
              title: 'Il bot garantisce profitti?',
              body: '<p><strong>No.</strong> Nessun bot o strategia puo garantire profitti. I mercati delle criptovalute sono estremamente volatili e imprevedibili. CryptoBot e uno strumento di supporto, non una garanzia di guadagno.</p>',
            },
            {
              title: 'Cosa succede se il bot si disconnette?',
              body: '<p>Il bot ha un sistema di riconnessione automatica. Se la connessione con Binance si interrompe, riprova dopo pochi secondi. Le posizioni aperte mantengono i livelli di Stop Loss e Take Profit e vengono controllate a ogni ciclo.</p>',
            },
            {
              title: 'Come resetto il portafoglio simulato?',
              body: '<p>Nella Dashboard, clicca il pulsante <strong>Resetta Portafoglio</strong>. Il saldo tornera al valore iniziale e tutte le posizioni e lo storico dei trade simulati verranno cancellati.</p>',
            },
            {
              title: 'Posso tenere piu strategie attive insieme?',
              body: '<p>Si. Puoi attivare tutte le strategie contemporaneamente. Il bot raccogliera i segnali da ciascuna e agira di conseguenza. Tieni presente che segnali contrastanti (es. una strategia dice BUY e un\'altra dice SELL) vengono eseguiti entrambi.</p>',
            },
            {
              title: 'Ogni quanto il bot controlla il mercato?',
              body: '<p>Il ciclo di analisi si ripete ogni 60 secondi. I prezzi invece vengono aggiornati continuamente in tempo reale tramite WebSocket.</p>',
            },
            {
              title: 'Cosa significano SL e TP?',
              body: `<ul>
                <li><strong>SL (Stop Loss)</strong> — Il livello di prezzo sotto il quale la posizione viene chiusa automaticamente per limitare la perdita</li>
                <li><strong>TP (Take Profit)</strong> — Il livello di prezzo sopra il quale la posizione viene chiusa automaticamente per incassare il profitto</li>
              </ul>
              <p>Entrambi vengono impostati automaticamente dal bot quando apre una posizione, in base ai parametri configurati nella pagina Strategie.</p>`,
            },
          ],
        },
        {
          title: '10. Glossario',
          body: `<table>
            <tr><th>Termine</th><th>Significato</th></tr>
            <tr><td><strong>Paper / Simulato</strong></td><td>Modalita di prova con denaro virtuale</td></tr>
            <tr><td><strong>Live</strong></td><td>Modalita con denaro reale su Binance</td></tr>
            <tr><td><strong>PnL</strong></td><td>Profit and Loss — Profitto e Perdita</td></tr>
            <tr><td><strong>Stop Loss (SL)</strong></td><td>Prezzo al quale la posizione viene chiusa per limitare la perdita</td></tr>
            <tr><td><strong>Take Profit (TP)</strong></td><td>Prezzo al quale la posizione viene chiusa per incassare il profitto</td></tr>
            <tr><td><strong>SMA</strong></td><td>Simple Moving Average — Media Mobile Semplice</td></tr>
            <tr><td><strong>EMA</strong></td><td>Exponential Moving Average — Media Mobile Esponenziale</td></tr>
            <tr><td><strong>RSI</strong></td><td>Relative Strength Index — Indice di Forza Relativa (0-100)</td></tr>
            <tr><td><strong>MACD</strong></td><td>Moving Average Convergence Divergence — Indicatore di momentum</td></tr>
            <tr><td><strong>Position sizing</strong></td><td>Calcolo della dimensione dell'investimento per singola operazione</td></tr>
            <tr><td><strong>USDT</strong></td><td>Tether — Stablecoin ancorata al dollaro americano</td></tr>
            <tr><td><strong>Candela (Kline)</strong></td><td>Rappresentazione grafica del prezzo in un intervallo di tempo</td></tr>
          </table>`,
        },
      ],
    }
  }

  // ---- English ----
  return {
    title: 'User Manual',
    disclaimer:
      'This software is provided for educational and experimental purposes only. Cryptocurrency trading involves significant risks, including total loss of invested capital. CryptoBot does not constitute financial advice. Use this bot at your own risk.',
    sections: [
      {
        title: '1. Overview',
        body: 'CryptoBot is an automated cryptocurrency trading bot connected to Binance. It analyses the market in real time, applies technical analysis strategies, and places orders autonomously. You can monitor everything from this web interface, on both desktop and mobile.',
        subsections: [
          {
            title: 'What it can do',
            body: `<ul>
              <li>Operate in <strong>Paper</strong> (simulated, no risk) or <strong>Live</strong> (real orders) mode</li>
              <li>Display real-time price updates</li>
              <li>Apply configurable strategies: SMA Crossover, RSI Reversal, MACD Crossover</li>
              <li>Automatically manage Stop Loss and Take Profit on every position</li>
              <li>Limit risk per trade (position sizing)</li>
              <li>Export trade history in CSV format</li>
            </ul>`,
          },
        ],
      },
      {
        title: '2. Paper vs Live Mode',
        body: `<p>Each user can choose their operating mode from the <strong>Settings</strong> page. The active mode is shown on the Dashboard with a coloured badge.</p>
        <table>
          <tr><th>Paper (Simulated)</th><th>Live (Real)</th></tr>
          <tr><td>Virtual portfolio — no real money involved</td><td>Real orders sent to Binance using your API keys</td></tr>
          <tr><td>Uses real market prices for simulation</td><td>Operates directly on your Binance account</td></tr>
          <tr><td>Requires Testnet API keys</td><td>Requires Live API keys</td></tr>
          <tr><td>Portfolio can be reset</td><td>Operations are not reversible</td></tr>
        </table>
        <p>Each user has their own portfolio and data, completely separate from other users. Paper and Live data never mix.</p>`,
        subsections: [
          {
            title: 'Live mode warning',
            body: '<p>In Live mode, every bot operation involves real money on your Binance account. Make sure you have tested your configuration in Paper mode for an adequate period first.</p>',
          },
        ],
      },
      {
        title: '3. Dashboard',
        body: '<p>The Dashboard is the main screen. From here you can monitor the overall status of the bot and your operations.</p>',
        subsections: [
          {
            title: '3.1 Stats Cards',
            body: `<p>The four cards at the top provide an instant summary:</p>
            <ul>
              <li><strong>Cash Balance</strong> — Available USDT, not tied up in open positions</li>
              <li><strong>Total Equity</strong> — Available cash + current value of all open positions</li>
              <li><strong>Total PnL</strong> — Overall profit or loss since the start. Green means profit, red means loss</li>
              <li><strong>Win Rate</strong> — Percentage of trades closed in profit, with the exact win/loss count</li>
            </ul>`,
          },
          {
            title: '3.2 Status Bar',
            body: `<p>Below the stats you'll find operational information:</p>
            <ul>
              <li><strong>Engine</strong> — Whether the bot is running or stopped</li>
              <li><strong>Symbol</strong> — The current trading pair (e.g. BTCUSDT)</li>
              <li><strong>Last Price</strong> — The most recent price received from the market</li>
            </ul>
            <p>In Paper mode, two additional buttons appear:</p>
            <ul>
              <li><strong>Reset Portfolio</strong> — Restores the virtual balance to its initial value, clearing all positions and history. Useful for starting fresh after changing strategies</li>
              <li><strong>Export CSV</strong> — Downloads a file with the complete trade history, which can be opened in Excel or Google Sheets for further analysis</li>
            </ul>`,
          },
          {
            title: '3.3 Open Positions',
            body: `<p>A table with all currently active operations. For each position you can see:</p>
            <ul>
              <li><strong>Symbol</strong> — The trading pair</li>
              <li><strong>Quantity</strong> — How much was purchased</li>
              <li><strong>Entry Price</strong> — The price at which the position was opened</li>
              <li><strong>Current Price</strong> — The current market price</li>
              <li><strong>PnL</strong> — Unrealised gain or loss</li>
              <li><strong>SL / TP</strong> — Stop Loss (in red) and Take Profit (in green) levels</li>
            </ul>`,
          },
          {
            title: '3.4 Recent Trades',
            body: '<p>History of recent operations, both open and closed. For each trade you can see entry and exit prices, realised profit/loss in USDT and percentage, and which strategy generated it.</p>',
          },
        ],
      },
      {
        title: '4. Strategies & Parameters',
        body: '<p>On the <strong>Strategies</strong> page you can control which algorithms the bot uses to decide when to buy or sell, and with which parameters.</p>',
        subsections: [
          {
            title: '4.1 Available Strategies',
            body: `<p>Each strategy has an <strong>ON/OFF</strong> toggle and numeric fields to adjust its behaviour.</p>
            <ul>
              <li><strong>SMA Crossover</strong> (Moving Average Crossover) — Compares a fast moving average with a slow one. When the fast crosses above the slow, the bot interprets it as a buy opportunity. The reverse generates a sell signal.
                <ul>
                  <li><em>fast_period</em> — Number of candles for the fast average (default: 10)</li>
                  <li><em>slow_period</em> — Number of candles for the slow average (default: 30)</li>
                </ul>
              </li>
              <li><strong>RSI Reversal</strong> — RSI measures whether an asset is "overbought" or "oversold". When RSI drops below the oversold threshold and then rises back, the bot generates a buy signal. When it rises above the overbought threshold and drops back, it generates a sell signal.
                <ul>
                  <li><em>period</em> — RSI calculation period (default: 14)</li>
                  <li><em>oversold</em> — Oversold threshold (default: 30)</li>
                  <li><em>overbought</em> — Overbought threshold (default: 70)</li>
                </ul>
              </li>
              <li><strong>MACD Crossover</strong> — MACD is a momentum indicator. When the MACD line crosses above the signal line, the bot buys. The opposite triggers a sell.
                <ul>
                  <li><em>fast</em> — Fast EMA period (default: 12)</li>
                  <li><em>slow</em> — Slow EMA period (default: 26)</li>
                  <li><em>signal</em> — Signal line period (default: 9)</li>
                </ul>
              </li>
            </ul>
            <p>You can edit values directly in the fields and the bot will apply them from the next cycle (every 60 seconds). You can also keep multiple strategies active at the same time.</p>`,
          },
          {
            title: '4.2 Risk Management',
            body: `<p>Below the strategies you'll find risk management parameters, which control how much the bot can risk per operation:</p>
            <ul>
              <li><strong>Max Position Size (%)</strong> — The maximum percentage of total capital the bot can invest in a single trade. Example: with 10,000 USDT equity and a 2% limit, each trade will be at most 200 USDT</li>
              <li><strong>Stop Loss (%)</strong> — How far the price can drop from the buy point before the bot automatically closes the position to limit losses. Example: 3% means if the bot buys at 50,000, it closes at 48,500</li>
              <li><strong>Take Profit (%)</strong> — How far the price must rise to close the position and lock in profit. Example: 5% means auto-close at 52,500 if the bot bought at 50,000</li>
            </ul>
            <p>After editing values, click <strong>Save Risk Parameters</strong> to apply them.</p>`,
          },
        ],
      },
      {
        title: '5. Logs & Execution',
        body: '<p>The <strong>Logs</strong> page lets you see in detail what the bot is doing moment by moment.</p>',
        subsections: [
          {
            title: '5.1 Recent Signals',
            body: `<p>Every time a strategy identifies an opportunity, the signal is logged here with:</p>
            <ul>
              <li><strong>Time</strong> — When it was generated</li>
              <li><strong>Type</strong> — BUY or SELL, colour-coded</li>
              <li><strong>Symbol</strong> — Which pair (e.g. BTCUSDT)</li>
              <li><strong>Price</strong> — The price at the time of the signal</li>
              <li><strong>Strategy</strong> — Which algorithm generated it</li>
              <li><strong>Reason</strong> — Technical explanation (e.g. "SMA golden cross")</li>
            </ul>
            <p>Data refreshes automatically every 5 seconds.</p>`,
          },
          {
            title: '5.2 Order History',
            body: `<p>Here you'll find all orders the bot has sent (or simulated), with their outcome:</p>
            <ul>
              <li><strong>FILLED</strong> (green) — Order executed successfully</li>
              <li><strong>PENDING</strong> (yellow) — Order awaiting execution (for limit orders not yet filled)</li>
              <li><strong>FAILED</strong> (red) — Order failed. The error message is visible in the "Error" column</li>
            </ul>
            <p>If an order fails, the engine logs the error and continues operating normally.</p>`,
          },
        ],
      },
      {
        title: '6. How the Bot Works',
        body: `<p>The bot runs an automatic cycle every 60 seconds:</p>
        <ol>
          <li><strong>Reads the market</strong> — Downloads the latest price data (1-minute candles) from Binance</li>
          <li><strong>Checks open positions</strong> — If a position has reached its Stop Loss or Take Profit level, it closes it automatically</li>
          <li><strong>Runs strategies</strong> — All active strategies analyse the data and decide whether to generate buy or sell signals</li>
          <li><strong>Executes orders</strong> — If a signal is generated, the bot calculates how much to invest (respecting risk limits), sets SL and TP, and places the order</li>
        </ol>
        <p>In parallel, prices are continuously updated in real time via a WebSocket connection to Binance.</p>`,
      },
      {
        title: '7. Practical Tips',
        body: `<ol>
          <li><strong>Always start in Paper mode</strong> — Get familiar with the bot without risking real money. Observe how it behaves for at least a few days</li>
          <li><strong>Check signals on the Logs page</strong> — Verify that the bot's decisions make sense relative to the market</li>
          <li><strong>Experiment with parameters</strong> — Try different SMA period combinations, RSI thresholds, and Stop Loss / Take Profit percentages</li>
          <li><strong>Use CSV export to analyse results</strong> — Open the file in a spreadsheet to understand which strategies work best</li>
          <li><strong>Switch to Live only when satisfied</strong> — And even then, keep position sizes small</li>
          <li><strong>Never invest more than you can afford to lose</strong> — No bot eliminates market risk</li>
        </ol>`,
      },
      {
        title: '8. Settings & Security',
        body: '<p>On the <strong>Settings</strong> page (visible to admin and user roles, not guests) you can configure your personal account.</p>',
        subsections: [
          {
            title: '8.1 Binance API Keys',
            body: `<p>Each user enters their own Binance API keys. Keys are personal and separate from other users.</p>
            <ul>
              <li><strong>Testnet</strong> — For paper trading. Get them at testnet.binance.vision</li>
              <li><strong>Live</strong> — For real trading. Enable only "Spot &amp; Margin Trading", never "Withdrawals"</li>
            </ul>
            <p>Keys are stored securely in the server database and are never fully visible after saving.</p>`,
          },
          {
            title: '8.2 Trading Mode',
            body: '<p>Each user can independently choose whether to operate in <strong>Paper</strong> or <strong>Live</strong> mode. You can be in Paper while another user is in Live.</p>',
          },
          {
            title: '8.3 Initial Capital',
            body: '<p>You can configure the initial capital for your virtual portfolio (paper trading). To apply the new value, reset your portfolio from the Dashboard.</p>',
          },
          {
            title: '8.4 Trading Schedule',
            body: '<p>You can set a time window during which the bot operates (e.g. 08:00 to 22:00 UTC). Outside this window the bot continues monitoring open positions for Stop Loss and Take Profit, but does not open new positions. Leave fields empty to trade 24/7.</p><p>Overnight ranges are supported (e.g. 22:00 - 08:00). Times are in UTC format.</p>',
          },
          {
            title: '8.5 Auto Logout',
            body: '<p>For security, the system automatically logs you out after a period of inactivity (default: 30 minutes). Inactivity is measured by mouse, keyboard, and touch events. When you return, you will see the login page.</p>',
          },
        ],
      },
      {
        title: '9. FAQ',
        body: '',
        subsections: [
          {
            title: 'Does the bot guarantee profits?',
            body: '<p><strong>No.</strong> No bot or strategy can guarantee profits. Cryptocurrency markets are extremely volatile and unpredictable. CryptoBot is a support tool, not a guaranteed income source.</p>',
          },
          {
            title: 'What happens if the bot disconnects?',
            body: '<p>The bot has an automatic reconnection system. If the connection to Binance drops, it retries after a few seconds. Open positions keep their Stop Loss and Take Profit levels and are checked on every cycle.</p>',
          },
          {
            title: 'How do I reset the simulated portfolio?',
            body: '<p>On the Dashboard, click the <strong>Reset Portfolio</strong> button. The balance will return to its initial value and all simulated positions and trade history will be cleared.</p>',
          },
          {
            title: 'Can I keep multiple strategies active at once?',
            body: '<p>Yes. You can activate all strategies at the same time. The bot will collect signals from each and act accordingly. Keep in mind that conflicting signals (e.g. one strategy says BUY and another says SELL) will both be executed.</p>',
          },
          {
            title: 'How often does the bot check the market?',
            body: '<p>The analysis cycle repeats every 60 seconds. Prices, however, are updated continuously in real time via WebSocket.</p>',
          },
          {
            title: 'What do SL and TP mean?',
            body: `<ul>
              <li><strong>SL (Stop Loss)</strong> — The price level below which the position is automatically closed to limit the loss</li>
              <li><strong>TP (Take Profit)</strong> — The price level above which the position is automatically closed to lock in profit</li>
            </ul>
            <p>Both are set automatically by the bot when opening a position, based on the parameters configured on the Strategies page.</p>`,
          },
        ],
      },
      {
        title: '10. Glossary',
        body: `<table>
          <tr><th>Term</th><th>Meaning</th></tr>
          <tr><td><strong>Paper</strong></td><td>Simulated mode with virtual money</td></tr>
          <tr><td><strong>Live</strong></td><td>Real mode with actual money on Binance</td></tr>
          <tr><td><strong>PnL</strong></td><td>Profit and Loss</td></tr>
          <tr><td><strong>Stop Loss (SL)</strong></td><td>Price at which the position is closed to limit loss</td></tr>
          <tr><td><strong>Take Profit (TP)</strong></td><td>Price at which the position is closed to lock in profit</td></tr>
          <tr><td><strong>SMA</strong></td><td>Simple Moving Average</td></tr>
          <tr><td><strong>EMA</strong></td><td>Exponential Moving Average</td></tr>
          <tr><td><strong>RSI</strong></td><td>Relative Strength Index (0-100)</td></tr>
          <tr><td><strong>MACD</strong></td><td>Moving Average Convergence Divergence — momentum indicator</td></tr>
          <tr><td><strong>Position sizing</strong></td><td>Calculating the investment size for a single operation</td></tr>
          <tr><td><strong>USDT</strong></td><td>Tether — stablecoin pegged to the US dollar</td></tr>
          <tr><td><strong>Candle (Kline)</strong></td><td>Graphical representation of price over a time interval</td></tr>
        </table>`,
      },
    ],
  }
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function Manual() {
  const { lang } = useLang()
  const content = manual(lang)

  return (
    <div className="max-w-4xl mx-auto space-y-8">
      <h1 className="text-2xl font-bold text-white">{content.title}</h1>

      {/* Disclaimer */}
      <div className="bg-yellow-900/30 border border-yellow-700/50 rounded-lg p-4 text-sm text-yellow-200">
        <strong>{lang === 'it' ? 'Avvertenza' : 'Disclaimer'}:</strong>{' '}
        {content.disclaimer}
      </div>

      {/* Table of contents */}
      <nav className="bg-gray-900 border border-gray-800 rounded-lg p-4">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-2">
          {lang === 'it' ? 'Indice' : 'Table of Contents'}
        </h2>
        <ul className="space-y-1">
          {content.sections.map((s, i) => (
            <li key={i}>
              <a
                href={`#section-${i}`}
                className="text-sm text-blue-400 hover:text-blue-300 transition-colors"
              >
                {s.title}
              </a>
            </li>
          ))}
        </ul>
      </nav>

      {/* Sections */}
      {content.sections.map((section, i) => (
        <section key={i} id={`section-${i}`} className="scroll-mt-20">
          <h2 className="text-xl font-semibold text-white mb-3 border-b border-gray-800 pb-2">
            {section.title}
          </h2>

          {section.body && (
            <div
              className="manual-content text-sm text-gray-300 leading-relaxed"
              dangerouslySetInnerHTML={{ __html: section.body }}
            />
          )}

          {section.subsections?.map((sub, j) => (
            <div key={j} className="mt-4 ml-2 pl-4 border-l-2 border-gray-800">
              <h3 className="text-base font-medium text-gray-100 mb-2">{sub.title}</h3>
              <div
                className="manual-content text-sm text-gray-300 leading-relaxed"
                dangerouslySetInnerHTML={{ __html: sub.body }}
              />
            </div>
          ))}
        </section>
      ))}
    </div>
  )
}
