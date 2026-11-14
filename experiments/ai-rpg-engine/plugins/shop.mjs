// Trusted, stateless first-party contribution; the host owns validation and settlement.
export function invoke({capability,session,input={}}){
 if(capability==='ui.shop-action'&&Object.keys(input).length===0)return {kind:'shop-action',action:'shop',label:'街角杂货店'};
 if(capability==='session.shop.purchase'&&Object.keys(input).sort().join(',')==='actionHash'&&/^[a-f0-9]{64}$/.test(input.actionHash))return {kind:'shop-intent',sessionId:session.id,actionHash:input.actionHash};
 throw Error('INVALID_SHOP_REQUEST');
}
