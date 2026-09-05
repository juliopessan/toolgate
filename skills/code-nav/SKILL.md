---
name: code-nav
description: Navegação estrutural de código via AST (ast-grep/tree-sitter) para achar símbolos, funções e padrões lendo o mínimo de tokens. Use antes de varrer arquivos inteiros em bases grandes, ou quando o usuário pedir busca estrutural.
---

# Code Nav (AST)

AST = Abstract Syntax Tree (Árvore de Sintaxe Abstrata): representação em árvore da estrutura lógica do código-fonte, usada aqui via ast-grep/tree-sitter para navegar com precisão sintática em vez de tratar o código como texto plano.

Objetivo FinOps: substituir "ler arquivos inteiros" por consultas estruturais — menos tokens antes mesmo de compressão.

## Setup (uma vez por máquina)
```bash
brew install ast-grep   # ou: npm i -g @ast-grep/cli
```

## Padrões de uso

Buscar uma função/símbolo em vez de abrir arquivos:
```bash
ast-grep run -p 'function $NAME($$$) { $$$ }' --lang ts src/   # definições TS
ast-grep run -p 'def $NAME($$$):' --lang py .                  # definições Python
ast-grep run -p 'useEffect($$$)' --lang tsx src/               # usos de hook
```

Extrair só o corpo de um símbolo específico:
```bash
ast-grep run -p 'def process_invoice($$$): $$$' --lang py --json | head -50
```

## Regras
1. Em bases > ~50 arquivos, tente `ast-grep` antes de `Read` em arquivos inteiros.
2. Leia apenas o trecho retornado (linha inicial/final do match) via Read com offset/limit.
3. Registre economias relevantes na tabela `savings` (source='ast') estimando tokens evitados (tamanho do arquivo − trecho lido, /4).
