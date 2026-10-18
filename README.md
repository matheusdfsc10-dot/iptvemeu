# IPTV pessoal

Playlists geradas a partir do [iptv-org](https://github.com/iptv-org/iptv) e do Pluto TV Brasil,
atualizadas todo dia por GitHub Actions (canais fora do ar são removidos automaticamente).

| Lista | O que tem |
|---|---|
| `brasil.m3u` | Brasil (abertos, notícias, esportes, filmes, séries, novelas, infantil...), Portugal e lusófonos, Pluto TV BR |
| `completa.m3u` | Tudo da `brasil.m3u` + canais do mundo inteiro, agrupados por país |

No IPTV Smarters: *Adicionar usuário → Carregar playlist ou arquivo/URL → M3U URL* e colar o link raw do arquivo.

Rodar na mão: `python build.py --strict` (o `--strict` descarta também os bloqueados por região; use só rodando do Brasil).
