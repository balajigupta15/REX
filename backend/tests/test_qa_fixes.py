"""Regression cases for QA findings: validation, snapshots, paging, constraints and paper-watch recovery."""
import pytest
from fastapi.testclient import TestClient
from app import config, db, journal, memory, strategy_memory
from app.main import app
from app.aibuilder import api, agent, engine, service, store, synth


@pytest.fixture(autouse=True)
def qa_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DB_PATH', tmp_path / 'qa.sqlite')
    monkeypatch.setattr(config, 'MEKO_TOKEN', '')
    monkeypatch.setenv('REX_STORE', str(tmp_path / 'store'))
    monkeypatch.setenv('REX_OFFLINE', '1')
    monkeypatch.setenv('REX_CRYPTO_OFFLINE', '1')
    monkeypatch.setenv('REX_NEWS_OFFLINE', '1')
    monkeypatch.delenv('REX_DB_URL', raising=False)
    db.init(force=True)


client = TestClient(app, raise_server_exceptions=False)
SPEC = dict(ticker='TCS.NS', template='breakout', params={'n':20}, exits={'hold_days':10, 'stop_pct':3, 'tp_pct':8}, cost_bps=15)
CSV = 'symbol,trade_date,trade_type,quantity,price\nTCS,2026-10-01,buy,1,100\nTCS,2026-10-02,sell,1,110\n'


@pytest.mark.parametrize('trade', [dict(entry_price='abc'), dict(qty='abc'), dict(qty=0), dict(qty=-1), dict(side='other')])
def test_crypto_trade_fields_reject_invalid_input_without_effect(trade):
    r=client.post('/crypto/tracker/start',json=dict(symbol='BTCUSDT', interval='1h', template='breakout', trade=trade))
    assert r.status_code==422, r.text
    assert client.get('/crypto/tracker/sessions').json()['sessions']==[]


@pytest.mark.parametrize('cost', ['abc', None, {}, []])
def test_import_cost_validation_is_422(cost):
    r=client.post('/trades/import',json=dict(csv_text=CSV,cost_bps=cost))
    assert r.status_code==422, r.text
    assert client.get('/trades').json()['n']==0


def test_malformed_import_json_is_422():
    assert client.post('/trades/import',content='{',headers={'content-type':'application/json'}).status_code==422


def test_result_reopens_snapshot_and_history_without_new_backtest():
    r=client.post('/backtest',json=SPEC)
    assert r.status_code==200,r.text
    result=r.json(); rid=result['saved_result_id']
    count=len(client.get('/memory/runs').json())
    assert client.get('/results/'+rid).json()==result
    assert len(client.get('/memory/runs').json())==count
    assert client.get('/results').json()['results'][0]['id']==rid
    assert client.get('/results?market=crypto').json()['total']==0
    assert client.get('/results?offset=1').json()['results']==[]
    assert client.get('/results/missing').status_code==404
    assert 'saved_result_id' not in client.post('/backtest',json={**SPEC,'remember':False}).json()


def test_crypto_refine_snapshot_preserves_exact_result():
    r=client.post('/crypto/refine',json=dict(symbol='ETHUSDT',interval='1d',template='breakout'))
    assert r.status_code==200,r.text
    assert client.get('/results/'+r.json()['saved_result_id']).json()==r.json()


def test_tracked_trade_and_journal_use_same_cost_basis():
    r=client.post('/paper/start',json={**SPEC,'trade':dict(entry_price=100,qty=1)})
    assert r.status_code==200,r.text
    closed=client.post('/paper/'+r.json()['id']+'/stop',json=dict(exit_price=110,add_to_journal=True)).json()['close']
    assert closed['net_pct']==closed['saved']['journal']['ret_pct']==9.85


def test_paging_preserves_all_runs_lessons_and_strategy_runs():
    from app import saved_results
    for n in range(5):
        saved_results.save('nse','backtest',{'spec':{'ticker':'TCS.NS'},'result':{'fixture':n}})
    page1=saved_results.history('nse',2,0); page2=saved_results.history('nse',2,2)
    assert page1['total']==5 and len(page2['results'])==2
    assert not ({r['id'] for r in page1['results']} & {r['id'] for r in page2['results']})
    from contextlib import closing
    with closing(memory._db()) as con:
        for n in range(5):
            con.execute('INSERT INTO lessons(ts,run_id,ticker,template,kind,text,tags) VALUES(?,?,?,?,?,?,?)',(n,str(n),'TCS.NS','breakout','qa','fixture','{}'))
        con.commit()
    assert memory.list_lessons(2,offset=2)['total']==5
    assert len(memory.list_lessons(2,offset=2)['lessons'])==2
    assert memory.list_lessons(2,kind='missing')['total']==0


def test_round_options_are_api_supported(monkeypatch):
    # The native run model is the boundary the UI's server-provided options must satisfy.
    from app.aibuilder.models import RunReq
    options=client.get('/ai/status').json()['round_options']
    assert options==[1,2,3,4]
    for n in options: assert RunReq(goal='Test BTC',rounds=n).rounds==n


def test_preferences_are_persisted_and_enforce_drawdown():
    target=dict(market='crypto',symbol='BTCUSDT',interval='1d',cost_bps=None)
    rid=agent.create('Test BTC',target,offline=True,preferences={'style':'momentum','max_drawdown_pct':1})
    assert store.get_run(rid)['params']['preferences']['max_drawdown_pct']==1
    setup=dict(service.get_setup(target),preferences={'max_drawdown_pct':1})
    ev=service.evaluate(setup,"def signals(df):\n    return pd.Series(1, index=df.index)\n",{},1,[])
    v=dict(result=ev['result'],stats=ev['stats'],exits=ev['exits'],verdict=ev['verdict'])
    risk=service.risk_review(v,setup,1,[])
    assert any('your 1%' in x for x in risk['vetoes'])


def test_style_limits_offline_candidate_families(monkeypatch):
    target=dict(market='crypto',symbol='BTCUSDT',interval='1d',cost_bps=None)
    setup=dict(service.get_setup(target),preferences={'style':'mean-reversion'})
    seen=[]
    real=synth.build_candidates
    def capture(interval,short,only=None):
        cands=real(interval,short,only); seen.extend(c['arch'] for c in cands); return cands
    monkeypatch.setattr(synth,'build_candidates',capture)
    synth.search(setup,top=1)
    assert seen and set(seen)<={'rsi_pullback','zscore_reversion'}


def test_watch_can_be_recovered_and_stopped_snapshot_is_frozen(monkeypatch):
    # Use a native stored version; fake observation isolates lifecycle from signal/data calculation.
    sid=store.create_session('QA watch','crypto','BTCUSDT','1h')
    v=store.save_version(dict(session_id=sid,parent_id=None,message='qa',title='QA',explanation='fixture',rules=[],code='def signals(df):\n    return df["close"]*0\n',exits={},source='offline_synth',model=None,market='crypto',symbol='BTCUSDT',interval='1h',cost_bps=10,status='ok',result={},verdict={},stats={},notes={},trial_sharpe=0))
    calls=[]
    def live(*args): calls.append(1); return dict(position='FLAT',paper={'trades':0,'return_pct':len(calls)})
    monkeypatch.setattr(service,'live_signal',live)
    r=client.post('/ai/versions/'+v['id']+'/track'); assert r.status_code==200,r.text
    wid=r.json()['watch_id']
    assert any(w['id']==wid for w in client.get('/ai/watches').json()['watches'])
    latest=client.get('/ai/watches/'+wid).json()['live']
    assert client.post('/ai/watches/'+wid+'/stop').status_code==200
    n=len(calls)
    stopped=client.get('/ai/watches/'+wid).json()
    assert stopped['status']=='stopped' and stopped['live']==latest and len(calls)==n


def test_tracker_preserves_structured_costs_and_journal_agrees():
    spec={**SPEC,'cost_model':'india_delivery','trade_value':100000,'slippage_bps':7,'trade':{'entry_price':100,'qty':1}}
    r=client.post('/paper/start',json=spec)
    assert r.status_code==200,r.text
    view=r.json()
    assert view['cost_model']=='india_delivery' and view['slippage_bps']==7 and view['trade_value']==100000
    assert 'Indian delivery' in view['cost_label']
    closed=client.post('/paper/'+view['id']+'/stop',json={'exit_price':110,'add_to_journal':True}).json()['close']
    assert closed['net_pct']==closed['saved']['journal']['ret_pct']
