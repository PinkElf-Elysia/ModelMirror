"""Rebuild the 1628 atlas from the original Cliopatria v0.2.1 zip. Requires Shapely 2.1."""
import argparse
import json
from math import cos, pi, radians, sin, tan
from pathlib import Path
from zipfile import ZipFile
from shapely import coverage_is_valid, coverage_simplify, make_valid, set_precision, union_all
from shapely.geometry import GeometryCollection, Point, Polygon, box, shape
from shapely.ops import polygonize

DATA = "https://github.com/Seshat-Global-History-Databank/cliopatria/tree/v0.2.1"
MONGOLS = "https://unesdoc.unesco.org/ark:/48223/pf0000130205"
NE = "https://doi.org/10.7312/columbia/9780231160704.003.0002"
JURCHEN = "https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?artiId=ART002336042"
BREGEL = "https://turkistanilibrary.com/content/historical-atlas-central-asia"
YEHE = "https://www.dpm.org.cn/court/lineage/226250.html"

def project(lon, lat):
    rho = 2152.96687 / tan(pi / 4 + radians(lat) / 2) ** .562508074
    angle = .562508074 * radians(lon - 98.3402468)
    return [round(406.375754 + rho * sin(angle), 2), round(-1053.94647 + rho * cos(angle), 2)]

def polygons(g):
    if g.geom_type == 'Polygon' and not g.is_empty:
        yield g
    elif hasattr(g, 'geoms'):
        for part in g.geoms:
            yield from polygons(part)

def svg_paths(geom):
    def ring(points):
        return 'M' + ' '.join('%.2f,%.2f' % tuple(project(*p)) for p in list(points.coords)[:-1]) + 'Z'
    return [ring(g.exterior) + ''.join(ring(h) for h in g.interiors) for g in polygons(geom)]

def read_records(archive):
    result = {}
    with ZipFile(archive) as z, z.open(z.namelist()[0]) as f:
        for line in f:
            if b'"type": "Feature",' not in line:
                continue
            feature = json.loads(line.rstrip().rstrip(b','))
            p = feature['properties']
            if p['FromYear'] <= 1628 <= p['ToYear']:
                result[p['Name']] = make_valid(shape(feature['geometry']))
    return result

def build(g, islands):
    records = []
    def add(id, name, geom, color, kind, anchor, note, source=DATA, overview=False):
        geom = union_all(list(polygons(geom)))
        assert geom.area > 0, id
        records.append((dict(id=id,name=name,color=color,kind=kind,at=project(*anchor),
                             note=note,source=source,overview=overview), set_precision(geom,.00001)))
    state, region, port = '政权范围', '活动地域', '沿海据点'
    # Towns and the narrow western corridor establish the opening military front.
    corridor = Polygon([(119.68,39.9),(119.46,40.21),(120.18,40.79),(120.84,41.3),
                        (121.28,41.32),(121.55,41.09),(121.25,40.62),(120.55,40.2),(119.9,39.86)])
    jin = g['Later Jin Dynasty']
    # Haixi conquests precede 1628. Extend the generalized northern edge through Yehe and Ula.
    haixi = Polygon([(123.25,41.85),(123.45,42.98),(124.1,43.46),(125.3,43.85),(126.15,44.46),
                     (126.85,44.35),(127.13,43.87),(126.9,43.11),(126.6,42.45),(125.6,41.76)])
    add('ming','大明',g['Ming Dynasty'].union(jin.intersection(corridor)),'#c79e82',state,(108,32),'明境底色含开局宁锦走廊。城旁守方读取当前存档。')
    add('joseon','李氏朝鲜',g['Joseon'],'#a4bc94',state,(127,37.8),'义州、平壤、汉城。东江镇皮岛另以明军海上据点标出。',NE)
    add('later-jin','后金',jin.difference(corridor).union(haixi),'#88adbf',state,(124.3,42),'沈阳、辽阳属后金；叶赫已于1619年被兼并，乌拉旧地亦已纳入。北缘按城址概绘。科尔沁盟地另列。',YEHE)
    # Split the two oasis courts across a single editorial transition east of Kucha.
    tarim = g['Eastern Moghulistan']
    eastern_oases = Polygon([(83.65,39.8),(84.25,41.15),(84.43,41.83),(84.95,42.64),(86.8,43.18),
                             (89.5,43.55),(91.4,43.33),(92.0,42.45),(90.8,41.22),(87.6,40.2)])
    add('turfan','吐鲁番·察力失',eastern_oases,'#ddad84',state,(87.7,42.1),'与叶尔羌分治，察力失即今焉耆。东部绿洲的势力线为概绘；哈密另列。',BREGEL,True)
    add('yarkand','叶尔羌汗国',tarim.difference(eastern_oases),'#cfb77e',state,(79.5,38.4),'叶尔羌、喀什噶尔、和阗、阿克苏同属西部汗廷的绿洲体系；东侧吐鲁番另列。',BREGEL)
    add('oirat','漠西卫拉特诸部',g['Oirat Confederation'],'#81b5a6',region,(85,46.3),'伊犁、塔尔巴哈台与额尔齐斯上游一带绘作诸部活动地域。汗国后来的扩张不计入开局。',MONGOLS)
    himalaya="https://ora.ox.ac.uk/objects/uuid%3Afcc23944-02df-4e39-8273-059e2bbde318/files/ma3e18098982e6f3757904283f9f852f0"
    add('ladakh','拉达克',Polygon([(76.42,34.62),(77,34.95),(78.23,35.23),(79.28,34.68),(79.23,33.82),(78.52,32.96),(77.83,33.24),(77.31,33.67),(76.64,34.13)]),'#c2a990',region,(77.59,34.17),'列城王廷单列。赞斯卡、古格在本时点不随拉达克整片设色。山地线为概绘。',himalaya)
    add('guge','古格',Polygon([(78.52,32.96),(79.23,33.82),(80.14,33.33),(80.78,32.39),(80.5,31.41),(79.49,31.04),(78.61,31.75)]),'#b7ab8a',region,(79.67,31.48),'札布让仍为古格王廷。拉达克征服发生于1630年前后。',himalaya)
    add('tsang','乌斯藏及藏地诸部',g['Tibet'],'#b9abc3',region,(91.2,32.7),'藏地诸地方势力并存；日喀则附近的藏巴政权与拉萨在图上另可查阅，区域底色只表地理。',MONGOLS)
    # The source's broad northern mesh has an anachronistic "Golden Horde" label.
    # Retain its shared geographical edges only. Internal divisions follow a single
    # Gobi transition, the western Liao basin, and an explicitly undivided northern margin.
    steppe = g['Golden Horde'].union(g['Mongol Khanate']).intersection(Polygon([
        (60,30),(122.5,30),(122.9,42.3),(124.2,44.3),(124,46.8),(123.4,48.5),
        (121.5,50.4),(121.7,53.5),(124,57),(60,57)]))
    north = Polygon([(75,45),(88,44.5),(94,44.6),(100,43.6),(106,42.3),(111,43.1),(115,44.8),
                     (118,46.1),(123,47.6),(130,49),(135,63),(72,63)])
    northern_rim = Polygon([(75,50),(88,50.6),(98,51.8),(107,52.5),(115,51.6),(125,50),(139,49.8),(144,65),(70,65)])
    khorchin = Polygon([(120.4,42.75),(122.35,42.85),(123.75,43.65),(125.05,45.4),(124.6,46.9),
                        (122.8,47.2),(121.2,46.7),(120.2,45.4),(119.9,44.1)])
    add('khorchin','科尔沁诸部',khorchin,'#b7b97e',region,(122.65,45.65),'嫩江西侧、西辽河以北的活动范围示意。与后金通婚结盟，保持独立标色。',YEHE,True)
    inner=Polygon([(118.15,42.38),(119.26,41.88),(120.47,42.1),(121.48,42.72),(121.7,43.9),
                   (121.5,45.12),(120.45,45.65),(119.25,45.06),(118.35,44.12)])
    add('inner-khalkha','内喀尔喀诸部',steppe.intersection(inner),'#c6b6aa',region,(120.2,43.55),
        '内喀尔喀所属的敖汉、奈曼于1627年归附后金；巴林、扎鲁特等关系变化另有时间差。只合示活动带，不合并入后金直辖。','https://www.dpm.org.cn/Uploads/pdf/1613/T00030_00.pdf')
    ordos=Polygon([(106.1,39.15),(106.65,40.65),(108.3,40.92),(110.65,40.48),(111.1,39.58),
                   (110.75,38.46),(109.1,37.62),(107.65,37.65)])
    add('ordos','鄂尔多斯部',steppe.intersection(ordos),'#c5ac85',region,(108.2,39.45),
        '按黄河河套以南活动地域概示。南缘依开局明境及边镇另行分区。',MONGOLS)
    kharachin = steppe.intersection(Polygon([(117.4,40.95),(119.3,40.75),(120.45,41.35),(120.4,42.75),(119.2,42.98),(117.85,42.42)]))
    add('kharachin','喀喇沁等部',kharachin,'#c9ae8c',region,(118.75,41.65),'辽西北、老哈河一带的诸部活动地。与察哈尔冲突及结盟的过程复杂，交界按河谷概画。',MONGOLS)
    chahar = Polygon([(111.4,40.6),(114.6,40.75),(117.1,41.5),(120.25,42.75),(122.8,43.7),
                      (124.3,47.7),(116.45,46.1),(111.2,43.5)])
    add('khalkha','漠北喀尔喀诸部',steppe.intersection(north).difference(northern_rim),'#a6b589',region,(104.5,47.4),'漠北各部合示；戈壁过渡带只是一条共用的制图分线。',MONGOLS)
    add('chahar','察哈尔与林丹汗',steppe.intersection(chahar).difference(north),'#c59f7d',region,(116.2,43.2),'西拉木伦河一带与西迁沿线。察哈尔、科尔沁和漠南其余诸部区别标色。1628年内迁徙，内部线为示意。',MONGOLS)
    add('southern-mongols','漠南诸部',steppe.difference(north),'#ccba91',region,(108.9,40.6),'归化、土默川一带为土默特活动地，连同未细分的漠南各部标示。鄂尔多斯、喀喇沁另列。',MONGOLS,True)
    add('north-woods','北缘山林诸部',steppe.intersection(north).intersection(northern_rim),'#bfd0b1',region,(107,53.3),'萨彦岭、贝加尔湖周边多部共同活动。北缘仅作地域提示。',MONGOLS)
    ne = Polygon([(119,41.5),(121,44.4),(119.7,47.3),(119.5,49.6),(121.3,51.5),(124.2,53.2),
                  (126.8,53.4),(130.3,50.6),(134.2,48.6),(137.2,50.1),(140.1,52),(140.7,49.2),
                  (138.8,45.5),(133.1,41.7),(130.6,41.6),(125,40.0)])
    east_forest = Polygon([(126.15,40.1),(126.7,43.4),(128.15,44.8),(129.2,46.6),(132.2,47.6),
                           (135.6,49.5),(141.8,53),(146,39)])
    add('east-jurchen','东海女真诸部',ne.intersection(east_forest),'#b3b8cb',region,(131.4,45.3),'牡丹江、乌苏里江一带有虎尔哈、瓦尔喀等部。早期征讨与迁户不能作为后金连续占领的证据。',JURCHEN,True)
    add('amur','嫩江·黑龙江诸部',ne,'#b0c7b7',region,(126.3,48.3),'嫩江与黑龙江中上游分布索伦、达斡尔等群体。以流域概画，不推定各聚落共同隶属。',NE,True)
    # Preserve minor neighbors around larger polygons. Source names are not necessarily period names.
    for id,name,key,color,anchor,note in [
      ('hami','哈密及天山东缘','Khoshut Khanate','#cbae97',(95,41.4),'哈密绿洲、东天山和戈壁通道。来源的大范围记录不足以确定1628年各处归属，此区标为待细分。'),
      ('qinghai','青海蒙古诸部','Tümed','#b0bdab',(97.5,37),'青海湖至柴达木周边诸部。1630年代以后的和硕特政权不前移。'),
      ('west-tianshan','西天山诸部','Western Moghulistan','#ceadac',(76.5,41.4),'帕米尔以北、西天山山地诸部，与南面绿洲汗廷区别表示。')]:
        add(id,name,g[key],color,region,anchor,note,MONGOLS,True)
    for id,key,name,color,anchor in [
      ('kazakh','Kazakh Khanate','哈萨克汗国','#a4c4ab',(75,46.8)),
      ('bukhara','Khanate of Bukhara','布哈拉汗国','#b4b2ce',(69.5,39.2)),
      ('mughal','Mughal Empire','莫卧儿帝国','#c7c18f',(80,26.6)),
      ('mrauk','Kingdom of Mrauk U','若开','#d2b989',(93.3,20.5)),
      ('mak','Đại Việt','高平莫氏','#b89aae',(106.1,22.6)),
      ('trinh','Trịnh lords','安南·郑主','#99bd94',(105.6,20.3)),
      ('nguyen','Nguyễn lords','安南·阮主','#dba577',(107.8,16.1)),
      ('toungoo','First Toungoo Empire','东吁王朝','#b5a3c6',(96.3,21.5)),
      ('lanxang','Lan Xang','澜沧','#afbf99',(102.5,18.8)),
      ('siam','Ayutthaya Kingdom','暹罗·阿瑜陀耶','#b4c5bd',(100.5,15.1)),
      ('champa','Chámpa','占城残部','#aeb1c2',(108.7,11.8)),
      ('khmer','Khmer Empire','柬埔寨','#d6c197',(104.4,12.5)),
      ('kathmandu','Kathmandu','加德满都谷地','#ccc2a7',(85.3,27.65)),
      ('gorkha','Gorkha Kingdom','喜马拉雅南麓诸邦','#c7b9ae',(82.5,29.5))]:
        add(id,name,g[key],color,region if id in ('kathmandu','gorkha') else state,anchor,
            '按1628年所在时段记录绘制。区域内地方政权及边地从略。')
    japan=g['Tokugawa Shogunate']
    add('tokugawa','德川幕府',japan.intersection(box(128.3,30.2,146,42.4)).difference(islands['ezo']).union(islands['ezo'].intersection(Polygon([(139.95,41.36),(140.5,41.38),(140.54,41.78),(140.12,41.83)]))),'#c69f9f',state,(137,35.7),'日本列岛主要部分。琉球王府另列。')
    add('ryukyu','琉球王国',japan.intersection(box(122.9,23.5,129,28)),'#c6ae86',state,(127.72,26.2),'首里王府延续，1609年萨摩入侵后受其控制。')
    for id,key,name,x,y,color in [('dutch','Dutch Republic','大员·荷兰',120.16,23,'#bd7651'),
                                  ('spanish','Spanish Empire','鸡笼·西班牙',121.8,25.16,'#877395')]:
        add(id,name,g[key].intersection(Point(x,y).buffer(.095)),color,port,(x,y),'沿海据点，岛内各社另计。')
    add('taiwan-communities','台湾原住民各社',islands['taiwan'],'#d3be9f',region,(120.95,23.78),
        '岛内及沿岸社群分布，各社有自己的领地。大员和鸡笼据点单列；岛内社界待细分。','https://tcmb.culture.tw/zh-tw/detail?id=258775&indexCode=Culture_Object')
    add('ezo','虾夷地·阿伊努各地',islands['ezo'],'#bdc5b0',region,(142.5,43.5),
        '北海道各地的阿伊努社群合注。道南松前氏的据点与交易权见另列地点。',NE)
    return records

def partition(records):
    # Node all candidate outlines once, assign every face only once, then simplify
    # the entire coverage together, keeping adjacent boundaries vertex-identical.
    lines = union_all([geom.boundary for _,geom in records])
    faces=[[] for _ in records]
    for face in polygonize(lines):
        point=face.representative_point()
        for i,(_,geom) in enumerate(records):
            if geom.covers(point):
                faces[i].append(face)
                break
    cells=[union_all(parts) for parts in faces]
    assert coverage_is_valid(cells), 'overlapping derived ranges'
    cells=coverage_simplify(cells,.045)
    assert coverage_is_valid(cells)
    return dict((row['id'],cell) for (row,_),cell in zip(records,cells))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    landfile = args.archive.parent / 'ne50-land.geojson'
    land = [p for f in json.loads(landfile.read_text(encoding='utf8'))['features'] for p in polygons(shape(f['geometry']))]
    islands={key: next(p for p in land if p.covers(Point(x,y))) for key,x,y in [('taiwan',120.97,23.76),('ezo',142.5,43.5)]}
    records=build(read_records(args.archive), islands)
    cells=partition(records)
    for a,b in [('ming','later-jin'),('later-jin','joseon'),('inner-khalkha','khorchin'),('chahar','inner-khalkha'),('chahar','khalkha'),
                ('khalkha','oirat'),('yarkand','turfan'),('yarkand','oirat'),('trinh','nguyen'),
                ('trinh','mak'),('ming','tsang'),('east-jurchen','amur'),('later-jin','east-jurchen'),('ladakh','guge'),('taiwan-communities','dutch')]:
        assert cells[a].boundary.intersection(cells[b].boundary).length>.05, (a,b)
    for id,x,y in [('ming',120.72,40.62),('ming',121.13,41.09),('later-jin',124.38,43.2),
                   ('later-jin',126.47,44.08),('khorchin',122.3,44.6),('east-jurchen',129.6,44.6),
                   ('amur',124.9,47.5),('turfan',89.19,42.94),('turfan',86.57,42.06),('yarkand',80.26,41.17),
                   ('yarkand',77.25,38.41),('oirat',81.3,43.9),('khalkha',102.84,47.2),('taiwan-communities',120.97,23.76),('ezo',142.5,43.5),('guge',79.67,31.48),('ladakh',77.59,34.17)]:
        assert cells[id].covers(Point(x,y)),(id,x,y)
    output=[]
    for row,_ in records:
        paths=svg_paths(cells[row['id']])
        assert paths,row['id']
        output.append({**row,'paths':paths})
    args.output.write_text(json.dumps(output,ensure_ascii=False,indent=1)+'\n',encoding='utf8',newline='\n')
    print(f'{len(output)} areas; shared edges and control points verified')

if __name__=='__main__':
    main()
