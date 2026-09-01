const fs = require('fs');

const source = 'C:/Users/2Class_08/Desktop/영수증 test/라벨링데이터-20260821T030610Z-1-001/라벨링데이터/test01_test20_ground_truth.json';
const destination = 'reports/test01_test20_ground_truth_receipts_taxonomy.json';
const mapping = {
  '의류/쇼핑': '취미/쇼핑',
  '생활/쇼핑': '취미/쇼핑',
  '식비': '식비',
  '꽃/식물': '취미/쇼핑',
  '카페/음료': '식비',
  '식품/쇼핑': '식비/쇼핑',
  '뷰티/쇼핑': '미용/생활',
  '의료': '의료',
};

const rows = JSON.parse(fs.readFileSync(source, 'utf8'));
for (const row of rows) {
  const original = row['카테고리'];
  if (!(original in mapping)) throw new Error(`Unmapped category: ${original}`);
  row['카테고리'] = mapping[original];
}
fs.writeFileSync(destination, `${JSON.stringify(rows, null, 2)}\n`, 'utf8');
console.log(JSON.stringify({ source, destination, count: rows.length, mapping }, null, 2));
