import {build} from 'esbuild';
import {mkdir,copyFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const root = fileURLToPath(new URL('../',import.meta.url));
await mkdir(`${root}/vendor`,{recursive:true});
for (const [name,source] of Object.entries({xlsx:"export * from 'xlsx';",word:"export * from 'docx'; export {saveAs} from 'file-saver';"})) {
  await build({stdin:{contents:source,resolveDir:root,sourcefile:`${name}-entry.js`},bundle:true,format:'esm',platform:'browser',target:['es2020'],minify:true,legalComments:'eof',outfile:`${root}/vendor/${name}.js`});
}
for (const [pkg,file] of [['xlsx','LICENSE'],['docx','LICENSE'],['file-saver','LICENSE.md']]) await copyFile(`${root}/node_modules/${pkg}/${file}`,`${root}/vendor/${pkg}-LICENSE.txt`);
console.log('Bundled local Excel and Word libraries. No CDN is used at runtime.');
