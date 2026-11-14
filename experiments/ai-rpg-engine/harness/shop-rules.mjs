import {clone,requireValue,integer,exact,id,hash} from './contracts.mjs';
export function validateRules(config){
 requireValue(config?.format==='modelmirror.rpg.shop-rules/1'&&typeof config.version==='string'&&config.version.length<100);
 requireValue(config.currency&&integer(config.currency.scale)&&config.currency.scale>0&&integer(config.currency.decimals)&&config.currency.decimals<=6);
 requireValue(config.currency.scale===10**config.currency.decimals&&typeof config.currency.label==='string');
 requireValue(Array.isArray(config.products)&&config.products.length>0&&config.products.length<=100);
 const seen=new Set();
 for(const p of config.products){requireValue(id(p.id)&&typeof p.name==='string'&&p.name.trim()&&integer(p.price)&&p.price>0&&integer(p.stock)&&!seen.has(p.id));seen.add(p.id);}
 return config;
}
export function validateAssets(assets,config){
 const keys=config.products.map(p=>p.id);
 requireValue(exact(assets,['balance','inventory','stock'])&&integer(assets.balance)&&exact(assets.inventory,keys)&&exact(assets.stock,keys),'SHOP_ASSETS_INVALID');
 requireValue([...Object.values(assets.inventory),...Object.values(assets.stock)].every(integer),'SHOP_ASSETS_INVALID');
 return assets;
}
export function initialAssets(config,balance=config.initial.balance,inventory=config.initial.inventory){
 return clone(validateAssets({balance,inventory,stock:Object.fromEntries(config.products.map(p=>[p.id,p.stock]))},config));
}
export function purchaseResult(assets,config,{productId,quantity}){
 validateAssets(assets,config);
 requireValue(id(productId)&&integer(quantity)&&quantity>0,'SHOP_PURCHASE_INVALID');
 const product=config.products.find(p=>p.id===productId);requireValue(product,'SHOP_PRODUCT_UNKNOWN');
 const total=product.price*quantity;requireValue(integer(total)&&integer(assets.inventory[productId]+quantity),'SHOP_AMOUNT_OVERFLOW');
 if(assets.stock[productId]<quantity)return {outcome:'rejected',reason:'SHOP_STOCK_INSUFFICIENT',productId,quantity,total,assets:clone(assets)};
 if(assets.balance<total)return {outcome:'rejected',reason:'SHOP_BALANCE_INSUFFICIENT',productId,quantity,total,assets:clone(assets)};
 const next=clone(assets);next.balance-=total;next.stock[productId]-=quantity;next.inventory[productId]+=quantity;
 return {outcome:'committed',reason:null,productId,quantity,total,assets:next,changes:{balance:-total,inventory:{[productId]:quantity},stock:{[productId]:-quantity}}};
}
