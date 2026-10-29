import sys, tempfile, unittest, sqlite3, time, json
from pathlib import Path
from datetime import datetime,timedelta
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import auth,database,workflow,allocations,beneficiary_needs as needs,data_reset

class Product(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();database.DATA_DIR=Path(self.tmp.name);database.DB_PATH=database.DATA_DIR/'savebite.db'
  database.init_db();auth.init_auth_db();workflow.init_workflow_db()
  auth.local_admin_setup('admin','Manager','AdminPassword123!')
  self.admin=auth.login('admin','AdminPassword123!')
  self.aid=auth.register_restaurant('alpha','Alpha','RestaurantAlpha123!')
  self.bid=auth.register_restaurant('bravo','Bravo','RestaurantBravo123!')
  self.a=auth.login('alpha','RestaurantAlpha123!');self.b=auth.login('bravo','RestaurantBravo123!')
  self.deadline=datetime.now()+timedelta(hours=6)
 def tearDown(self):self.tmp.cleanup()
 def donate(self,token):
  from locations import get_restaurants,canonical_pickup
  p=canonical_pickup(get_restaurants()[0])
  with auth.use_session(token):
   return database.insert_donation_record('Same Restaurant','Food',10,'Address',self.deadline,latitude=p['latitude'],longitude=p['longitude'],pickup_place_id=p['place_id'],pickup_place_name=p['name'],pickup_confirmed=True,food_category='cooked')
 def need(self):
  p=needs.get_beneficiaries()[0]
  with auth.use_session(self.admin):
   return needs.save_need(place_id=p['place_id'],address='Recipient address',category=needs.CATEGORIES[0],people=10,meals_requested=20,urgency=3,food_types=['cooked'],dietary_notes='',needed_by=self.deadline,confirmed=True)
 def reserve(self,d,n,token,request='reservation_token_12345',qty=7):
  with auth.use_session(token):
   r=next(x for x in needs.list_needs() if x['id']==n)
   return allocations.reserve(donation_id=d,need_id=n,quantity=qty,expected_need_revision=r['revision'],expected_available=database.get_donation(d)['remaining_quantity'],food_category='cooked',request_token=request,confirmed=True)
 def test_ownership_and_cross_account_replay(self):
  d=self.donate(self.a);d2=self.donate(self.b);self.assertNotEqual(d,d2);n=self.need();r=self.reserve(d,n,self.a)
  with auth.use_session(self.b):
   self.assertEqual([x['id'] for x in database.list_donations()],[d2]);self.assertEqual(allocations.list_allocations(),[])
   for call in [lambda:database.get_donation(d),lambda:workflow.get_allocation(r['id']),lambda:allocations.list_allocations(d),lambda:allocations.reserve(donation_id=d,need_id=n,quantity=7,expected_need_revision=1,expected_available=10,food_category='cooked',request_token='reservation_token_12345',confirmed=True),lambda:workflow.transition(allocation_id=r['id'],action='start',expected_revision=1,request_token='transition_token_123456',confirmed=True),lambda:data_reset.preview()]:
    with self.assertRaises(auth.AccessDenied):call()
   with self.assertRaises(auth.AccessDenied):
    p=needs.get_beneficiaries()[1];needs.save_need(place_id=p['place_id'],address='No',category=needs.CATEGORIES[0],people=1,meals_requested=1,urgency=1,food_types=['cooked'],dietary_notes='',needed_by=self.deadline,confirmed=True)
  with auth.use_session(self.admin):self.assertEqual(len(database.list_donations()),2);self.assertEqual(len(allocations.list_allocations()),1)
 def test_lifecycle_and_reset(self):
  d=self.donate(self.a);n=self.need();r=self.reserve(d,n,self.a)
  with auth.use_session(self.a):
   x=workflow.transition(allocation_id=r['id'],action='start',expected_revision=1,request_token='start_token_12345678',confirmed=True)
   y=workflow.transition(allocation_id=r['id'],action='deliver',expected_revision=x['revision'],request_token='deliver_token_1234567',confirmed=True)
   self.assertEqual(y['status'],'delivered');self.assertEqual(database.get_donation(d)['remaining_quantity'],3)
   replay=workflow.transition(allocation_id=r['id'],action='deliver',expected_revision=x['revision'],request_token='deliver_token_1234567',confirmed=True);self.assertEqual(replay['id'],y['id'])
   with self.assertRaises(ValueError):workflow.transition(allocation_id=r['id'],action='cancel',expected_revision=y['revision'],request_token='cancel_token_12345678',confirmed=True)
  with auth.use_session(self.admin):
   p=data_reset.preview();result=data_reset.reset_registered_data(expected_digest=p['digest'],confirmation='RESET')
   self.assertTrue(Path(result['backup']).is_file());self.assertEqual(database.list_donations(),[]);self.assertEqual(needs.list_needs(),[])
   self.assertEqual(len(auth.list_restaurants()),2)
  with auth.use_session(self.a):self.assertEqual(auth.current_user()['id'],self.aid)
 def test_cancel_restores_once_and_stale_digest(self):
  d=self.donate(self.a);n=self.need()
  with auth.use_session(self.admin):p=data_reset.preview()
  r=self.reserve(d,n,self.a)
  with auth.use_session(self.admin):
   with self.assertRaises(allocations.ReservationConflict):data_reset.reset_registered_data(expected_digest=p['digest'],confirmation='RESET')
  with auth.use_session(self.a):
   args=dict(allocation_id=r['id'],action='cancel',expected_revision=1,request_token='cancel_token_12345678',confirmed=True)
   workflow.transition(**args);workflow.transition(**args);self.assertEqual(database.get_donation(d)['remaining_quantity'],10)
   self.assertEqual(needs.list_needs()[0]['meals_reserved'],0)
 def test_session_isolation_revocation_and_expiry(self):
  def who(t):
   with auth.use_session(t):return auth.current_user()['id']
  with ThreadPoolExecutor(2) as pool:self.assertEqual(list(pool.map(who,[self.a,self.b]*5)),[self.aid,self.bid]*5)
  with auth.use_session(self.admin):auth.manage_restaurant(self.aid,active=False)
  with auth.use_session(self.a):
   with self.assertRaises(auth.AccessDenied):database.list_donations()
  with self.assertRaises(ValueError):auth.login('alpha','RestaurantAlpha123!')
  with database.get_connection() as con:con.execute('UPDATE auth_sessions SET expires_at=0 WHERE user_id=?',(self.bid,))
  with auth.use_session(self.b):
   with self.assertRaises(auth.AccessDenied):database.list_donations()
  with self.assertRaises(auth.AccessDenied):database.list_donations()
 def test_password_and_throttle(self):
  with auth.use_session(self.a):auth.change_password('RestaurantAlpha123!','NewRestaurantPassword123!')
  with auth.use_session(self.a):
   with self.assertRaises(auth.AccessDenied):auth.current_user()
  with self.assertRaises(ValueError):auth.login('alpha','RestaurantAlpha123!')
  token=auth.login('alpha','NewRestaurantPassword123!');auth.logout(token)
  with auth.use_session(token):
   with self.assertRaises(auth.AccessDenied):auth.current_user()
  for _ in range(5):
   with self.assertRaises(ValueError):auth.login('bravo','bad')
  with self.assertRaisesRegex(ValueError,'محاولات'):auth.login('bravo','RestaurantBravo123!')
  with database.get_connection() as con:
   row=con.execute("SELECT * FROM users WHERE username='alpha'").fetchone();self.assertNotIn('NewRestaurantPassword123!',row['password_hash']);self.assertEqual(row['role'],'restaurant')
 def test_legacy_data_not_claimed(self):
  d=self.donate(self.a)
  with database.get_connection() as con:con.execute('UPDATE donations SET owner_user_id=NULL WHERE id=?',(d,))
  auth.init_auth_db()
  with auth.use_session(self.a):self.assertEqual(database.list_donations(),[])
  with auth.use_session(self.admin):self.assertEqual(database.get_donation(d)['id'],d)

 def test_concurrent_reservations(self):
  d1=self.donate(self.a);d2=self.donate(self.b);n=self.need()
  def attempt(item):
   token,d,index=item
   with auth.use_session(token):
    try:
     return allocations.reserve(donation_id=d,need_id=n,quantity=7,expected_need_revision=1,expected_available=10,food_category='cooked',request_token='concurrent_reservation_'+str(index),confirmed=True)
    except allocations.ReservationConflict:
     return None
  with ThreadPoolExecutor(2) as pool:results=list(pool.map(attempt,[(self.a,d1,1),(self.b,d2,2)]))
  self.assertEqual(sum(x is not None for x in results),1)
  with auth.use_session(self.admin):
   self.assertEqual(sum(r['remaining_quantity'] for r in database.list_donations()),13)
   self.assertEqual(needs.list_needs()[0]['meals_reserved'],7)
 def test_preaccount_migration_backup(self):
  with tempfile.TemporaryDirectory() as tmp:
   old_dir,old_db=database.DATA_DIR,database.DB_PATH
   database.DATA_DIR=Path(tmp);database.DB_PATH=Path(tmp)/'legacy.db'
   try:
    database.init_db()
    with database.get_connection() as con:
     con.execute("INSERT INTO donations(donor_name,food_type,original_quantity,remaining_quantity,address,pickup_deadline,created_at) VALUES('Legacy','Food',9,9,'Address','2030-01-01 10:00:00','2026-10-29 10:00:00')")
    auth.init_auth_db();auth.init_auth_db()
    backups=list((Path(tmp)/'backups').glob('*.db'));self.assertEqual(len(backups),1)
    with sqlite3.connect(backups[0]) as con:self.assertEqual(con.execute('SELECT original_quantity FROM donations').fetchone()[0],9)
    with database.get_connection() as con:
     row=con.execute('SELECT * FROM donations').fetchone();self.assertEqual(row['donor_name'],'Legacy');self.assertIsNone(row['owner_user_id'])
   finally:
    database.DATA_DIR,database.DB_PATH=old_dir,old_db

if __name__=='__main__':unittest.main(verbosity=2)
