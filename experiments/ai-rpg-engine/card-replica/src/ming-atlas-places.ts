// Gazetteer for the opening in the seventh lunar month of 1628. Points are approximate.
type XY = [number, number];
export type AtlasPlace = {id:string; name:string; group:string; xy:XY; offset?:XY};
type Domain = {id:string; name:string; label:string; at:XY; width:number; note:string; source:string};
const mongols='https://unesdoc.unesco.org/ark:/48223/pf0000130205';
const vietnam='https://tulieuvankien.dangcongsan.vn/van-kien-tu-lieu-ve-dang/book/sach-chinh-tri/lich-su-tu-tuong-quan-su-viet-nam-tap-2-tu-nam-1428-den-nam-1858-326';
const taiwan='https://tcmb.culture.tw/zh-tw/detail?id=258775&indexCode=Culture_Object';
export const domains:Domain[]=[
 {id:'later-jin',name:'后金',label:'后金',at:[752,252],width:105,note:'皇太极驻沈阳。辽阳为旧都，赫图阿拉为早期都城。沿辽河核对城镇，再向西对读宁锦军报。',source:'https://www.dpm.org.cn/court/lineage/226251.html'},
 {id:'joseon',name:'李氏朝鲜',label:'李氏朝鲜',at:[803,348],width:170,note:'都汉城。义州在鸭绿江东岸，经平壤通往汉城。',source:'https://www.museum.go.kr/JPN/contents/E0403000000.do?relicId=1965'},
 {id:'chahar',name:'漠南察哈尔（林丹汗）',label:'察哈尔 · 林丹汗',at:[643,248],width:200,note:'所标为察哈尔在漠南的活动区域。漠南另有土默特、喀喇沁等部，各有其政治立场。',source:mongols},
 {id:'khalkha',name:'漠北喀尔喀诸部',label:'漠北喀尔喀',at:[479,178],width:240,note:'以鄂尔浑河谷一带帮助定位漠北诸部。游牧范围随季节与部众移动。',source:mongols},
 {id:'oirat',name:'漠西卫拉特诸部',label:'漠西卫拉特',at:[270,221],width:235,note:'伊犁河谷是辨识方位的地理参照。此时各部活动范围相互交错。',source:mongols},
 {id:'yarkand',name:'叶尔羌汗国',label:'叶尔羌汗国',at:[150,344],width:180,note:'汗廷以叶尔羌为中心，喀什噶尔亦为绿洲重镇。',source:mongols},
 {id:'tsang',name:'乌斯藏与藏地诸部',label:'乌斯藏及诸部',at:[270,504],width:165,note:'乌斯藏为地域名。藏巴政权的中心在日喀则，拉萨和西部诸地方各有其势力；整片底色仅表示藏地。',source:'https://www.hks.harvard.edu/sites/default/files/centers/cid/files/publications/faculty-working-papers/154.pdf'},
 {id:'trinh',name:'安南 · 后黎朝廷与郑主',label:'安南 · 郑主',at:[516,674],width:135,note:'后黎朝廷在升龙，北方由郑主掌权。郑、阮于1627年开战，高平另有莫氏。',source:vietnam},
 {id:'nguyen',name:'安南 · 阮主',label:'安南 · 阮主',at:[587,777],width:135,note:'阮主据顺化、广南一带，会安为沿海港市。图上分别标示南北中心。',source:vietnam},
 {id:'toungoo',name:'缅甸 · 东吁王朝',label:'东吁王朝',at:[363,727],width:180,note:'东吁王朝复兴时期。阿瓦在伊洛瓦底江上游，勃固在下缅甸。',source:'https://ignca.gov.in/Asi_data/4506.pdf'},
 {id:'tokugawa',name:'日本 · 德川幕府',label:'德川幕府',at:[946,361],width:260,note:'幕府在江户，朝廷在京都。大坂和长崎分别标出畿内和九州的城港。',source:'https://www.edotokyo.metro.tokyo.lg.jp/stories/08/'},
 {id:'spanish',name:'台湾北部 · 西班牙据点',label:'鸡笼 · 西班牙',at:[829,582],width:105,note:'1626年西班牙人在鸡笼社寮岛设据点。岛内有多个原住民社群。',source:taiwan},
 {id:'dutch',name:'台湾西南 · 荷兰据点',label:'大员 · 荷兰',at:[792,645],width:105,note:'荷兰东印度公司于1624年在大员设据点。标记仅表示西南沿海据点。',source:taiwan}
];
// Project longitude and latitude onto the original Ming sheet using a fitted conformal conic projection.
function project(lon:number,lat:number):XY {
 const rho=2152.96687 / Math.tan(Math.PI/4+lat*Math.PI/360)**0.562508074;
 const theta=0.562508074*(lon-98.3402468)*Math.PI/180;
 return [406.375754+rho*Math.sin(theta), -1053.94647+rho*Math.cos(theta)];
}
const domestic:[string,string,number,number][]=[
 ['nanjing','南京·应天府',715.1,460.6],['jinan','济南府',673.3,384.3],['taiyuan','太原府',608.6,371.4],
 ['kaifeng','开封府',644.2,423.5],['xian','西安府',562.7,443.5],['hangzhou','杭州府',739.9,485.4],
 ['nanchang','南昌府',684.3,528.5],['wuchang','武昌府',654.4,501.6],['fuzhou','福州府',743.6,561.5],
 ['guangzhou','广州府',655.8,629.2],['guilin','桂林府',600.2,599.5],['chengdu','成都府',496.8,512.8],
 ['guiyang','贵阳府',541.4,582.7],['yunnan-fu','云南府',478.6,611.8],['datong','大同府',609.4,333.9],['xuanfu','宣府镇',630,315.5]
];
const abroad:[string,string,string,number,number,XY?][]=[
 ['shenyang','沈阳','later-jin',123.43,41.80,[-10,-16]],['liaoyang','辽阳','later-jin',123.17,41.27,[9,10]],
 ['hetuala','赫图阿拉','later-jin',124.82,41.72,[13,-5]],['fushun','抚顺','later-jin',123.91,41.87,[0,-34]],
 ['sarhu','萨尔浒','later-jin',124.20,41.87,[25,24]],['guangning','广宁','later-jin',121.78,41.59,[-13,-10]],
 ['haizhou','海州','later-jin',122.75,40.85,[-9,15]],
 ['hanseong','汉城','joseon',126.98,37.57],['pyongyang','平壤','joseon',125.75,39.03],['uiju','义州','joseon',124.53,40.20],
 ['orkhon','鄂尔浑河谷','khalkha',102.84,47.2],['ili','伊犁河谷','oirat',81.3,43.9],
 ['yarkand-city','叶尔羌','yarkand',77.25,38.41],['kashgar','喀什噶尔','yarkand',75.99,39.47],
 ['shigatse','日喀则','tsang',88.88,29.27],['lhasa','拉萨','tsang',91.13,29.65],
 ['thanglong','升龙','trinh',105.85,21.03],['thuanhoa','顺化地区','nguyen',107.53,16.55],['hoian','会安','nguyen',108.33,15.88],
 ['pegu','勃固','toungoo',96.48,17.33],['ava','阿瓦','toungoo',96,21.85],
 ['edo','江户','tokugawa',139.75,35.68],['kyoto','京都','tokugawa',135.77,35.01,[-10,-8]],['osaka','大坂','tokugawa',135.50,34.69,[9,13]],['nagasaki','长崎','tokugawa',129.87,32.75],
 ['yehe','叶赫旧城','later-jin',124.38,43.2],['ula','乌拉旧城','later-jin',126.47,44.08],
 ['pidao','皮岛·东江镇','ming',124.65,39.55],['macao','濠镜·葡商居留地','ming',113.54,22.19],
 ['aksu','阿克苏','yarkand',80.26,41.17],['khotan','和阗','yarkand',79.93,37.11],
 ['turpan','吐鲁番','turfan',89.19,42.94],['karashahr','察力失·焉耆','turfan',86.57,42.06,[-12,15]],
 ['kumul','哈密绿洲','hami',93.51,42.82],['tarbagatai','塔尔巴哈台','oirat',83.1,46.7],
 ['mukden-steppe','西辽河','khorchin',122.2,43.7],['nengjiang','嫩江中游','amur',124.5,48.1],
 ['hurka','牡丹江流域','east-jurchen',129.6,44.6],['tumet','归化城·土默特','southern-mongols',111.66,40.81],
 ['shuri','首里','ryukyu',127.72,26.22],['caobang','高平','mak',106.25,22.67],
 ['keelung','鸡笼·社寮岛','spanish',121.8,25.16],['tayouan','大员','dutch',120.16,23.0]
];
export const referencePlaces:AtlasPlace[]=[
 ...domestic.map(([id,name,x,y]):AtlasPlace=>({id,name,group:'ming',xy:[x,y]})),
 ...abroad.map(([id,name,group,lon,lat,offset])=>({id,name,group,xy:project(lon,lat),offset}))
];
export function domainCenter(domain:Domain):XY {
 const sites=referencePlaces.filter(p=>p.group===domain.id);
 if(!sites.length)return domain.at;
 const xs=sites.map(p=>p.xy[0]),ys=sites.map(p=>p.xy[1]);
 return [(Math.min(...xs)+Math.max(...xs))/2,(Math.min(...ys)+Math.max(...ys))/2];
}
