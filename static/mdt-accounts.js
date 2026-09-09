const createDialog=document.getElementById('create-account');
document.getElementById('add-account').addEventListener('click',()=>createDialog.showModal());
document.getElementById('cancel-account').addEventListener('click',()=>createDialog.close());
document.getElementById('account-search').addEventListener('input',event=>{
  const query=event.target.value.trim().toLowerCase(); let matches=0;
  document.querySelectorAll('[data-account-search]').forEach(card=>{
    card.hidden=!card.dataset.accountSearch.toLowerCase().includes(query);
    if(!card.hidden) matches++;
  });
  document.getElementById('accounts-empty').hidden=matches>0;
});
let callsignRequest=0;
async function loadCallsigns(){
  const requestId=++callsignRequest;
  const rank=document.getElementById('new-account-rank').value;
  const select=document.getElementById('new-account-callsign');
  const status=document.getElementById('callsign-status');
  const submit=document.getElementById('create-account-submit');
  const retry=document.getElementById('retry-callsigns');
  select.replaceChildren(new Option(rank?'Checking availability…':'Choose a rank first',''));
  select.disabled=true; submit.disabled=true; retry.hidden=true; status.textContent='';
  if(!rank)return;
  try{
    const response=await fetch(`/mdt/accounts/callsigns?rank=${encodeURIComponent(rank)}`);
    if(requestId!==callsignRequest)return;
    if(response.redirected){status.textContent='Your session has expired. Sign in again.';return;}
    const data=await response.json();
    if(requestId!==callsignRequest)return;
    if(!response.ok)throw new Error(data.error || 'Unable to check callsigns. Retry.');
    select.replaceChildren(new Option('Select an available callsign',''));
    data.numbers.forEach(number=>select.add(new Option(`${data.prefix}-${number}`,String(number))));
    select.disabled=!data.numbers.length; submit.disabled=!data.numbers.length;
    status.textContent=data.numbers.length?`${data.numbers.length} available numbers`:'No callsigns available for this rank.';
  }catch(error){if(requestId!==callsignRequest)return;status.textContent=error.message;retry.hidden=false;}
}
document.getElementById('new-account-rank').addEventListener('change',loadCallsigns);
document.getElementById('retry-callsigns').addEventListener('click',loadCallsigns);
