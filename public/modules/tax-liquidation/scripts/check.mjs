import {readdir} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../',import.meta.url));
async function walk(path) {
  for(const entry of await readdir(path,{withFileTypes:true})) {
    if(['node_modules','vendor','__pycache__'].includes(entry.name))continue;
    const file=`${path}/${entry.name}`;
    if(entry.isDirectory())await walk(file);
    else if(/\.(?:js|mjs)$/.test(file)) {
      const result=spawnSync(process.execPath,['--check',file],{encoding:'utf8'});
      if(result.status!==0) {console.error(result.stderr);process.exit(1);}
    }
  }
}
await walk(root);
console.log('Tax liquidation module syntax OK.');
