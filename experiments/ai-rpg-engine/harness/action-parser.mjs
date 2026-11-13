// Deliberately closed grammar. Anything else goes to interpretation or narration.
export function exactPurchase(text,products){
 const match=/^(?:买|购买)\s*([1-9]\d*|[一二两三四五六七八九十])\s*(?:个|瓶|本|份)?\s*(饮用水|面包|笔记本)[。！!]?$/u.exec(text.trim());
 if(!match)return null;
 const quantity=Number(match[1])||({'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}[match[1]]);
 const product=products.find(p=>p.name===match[2]);return product&&Number.isSafeInteger(quantity)?{productId:product.id,quantity}:null;
}
