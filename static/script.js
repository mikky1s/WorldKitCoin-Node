// Конфигурация
const API_BASE = 'http://localhost:5000';
let currentPage = 1;
const PAGE_SIZE = 20;

// Форматирование времени
function formatTime(unix) {
    const d = new Date(unix * 1000);
    return d.toLocaleString('ru-RU');
}

// Сокращение хэша
function shortHash(hash, len = 10) {
    if (!hash) return '-';
    if (hash.length <= len*2) return hash;
    return hash.slice(0, len) + '...' + hash.slice(-len);
}

// Загрузка статистики
async function loadStats() {
    try {
        const res = await fetch(`${API_BASE}/info`);
        const data = await res.json();
        document.getElementById('height').textContent = data.height;
        document.getElementById('difficulty').textContent = data.difficulty_target ? '∞' : '-';
        document.getElementById('supply').textContent = data.total_supply / 1e8;
        document.getElementById('mempool').textContent = data.mempool_size;
        document.getElementById('utxoCount').textContent = data.utxo_count;
        document.title = `WKC Explorer (блок ${data.height})`;
    } catch (e) {
        console.error('Stats error:', e);
    }
}

// Загрузка хешрейта
async function loadHashrate() {
    try {
        const res = await fetch(`${API_BASE}/hashrate`);
        const data = await res.json();
        const hr = data.hashrate || 0;
        document.getElementById('hashrate').textContent = hr > 1e6 ? (hr/1e6).toFixed(1)+'M' : 
                                                         hr > 1e3 ? (hr/1e3).toFixed(1)+'K' : hr.toFixed(0);
    } catch (e) {
        console.error('Hashrate error:', e);
    }
}

// Загрузка блоков
async function loadBlocks(page) {
    const offset = (page - 1) * PAGE_SIZE;
    try {
        const res = await fetch(`${API_BASE}/blocks?limit=${PAGE_SIZE}&offset=${offset}`);
        const data = await res.json();
        const tbody = document.getElementById('blocksTableBody');
        tbody.innerHTML = '';
        if (data.blocks.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="text-center text-secondary">Нет блоков</td></tr>';
        } else {
            data.blocks.forEach(b => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td><a href="/block/${b.hash}">${b.height}</a></td>
                    <td><span class="hash-text"><a href="/block/${b.hash}">${shortHash(b.hash, 12)}</a></span></td>
                    <td>${formatTime(b.timestamp)}</td>
                    <td>${b.tx_count}</td>
                    <td>${b.nonce}</td>
                `;
                tbody.appendChild(tr);
            });
        }
        document.getElementById('blockCount').textContent = data.total;
        document.getElementById('pageInfo').textContent = `Страница ${page}`;
        document.getElementById('prevPageBtn').disabled = page <= 1;
        document.getElementById('nextPageBtn').disabled = offset + PAGE_SIZE >= data.total;
    } catch (e) {
        console.error('Blocks error:', e);
        document.getElementById('blocksTableBody').innerHTML = '<tr><td colspan="5" class="text-center text-danger">Ошибка загрузки</td></tr>';
    }
}

// Поиск
async function search(query) {
    if (!query || query.trim() === '') return;
    query = query.trim();
    // Попробуем как блок
    try {
        const res = await fetch(`${API_BASE}/block/${query}`);
        if (res.ok) {
            window.location.href = `/block/${query}`;
            return;
        }
    } catch (e) {}
    // Попробуем как транзакцию (у нас нет отдельного эндпоинта, но можно через историю)
    alert('Поиск транзакций пока доступен по хэшу блока. Для транзакций перейдите на страницу блока.');
}

// Инициализация
async function init() {
    await loadStats();
    await loadHashrate();
    await loadBlocks(currentPage);

    // Обновление каждые 15 секунд
    setInterval(() => {
        loadStats();
        loadHashrate();
    }, 15000);

    // Пагинация
    document.getElementById('prevPageBtn').addEventListener('click', () => {
        if (currentPage > 1) {
            currentPage--;
            loadBlocks(currentPage);
        }
    });
    document.getElementById('nextPageBtn').addEventListener('click', () => {
        currentPage++;
        loadBlocks(currentPage);
    });

    // Поиск
    document.getElementById('searchBtn').addEventListener('click', () => {
        const q = document.getElementById('searchInput').value;
        search(q);
    });
    document.getElementById('searchInput').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            const q = document.getElementById('searchInput').value;
            search(q);
        }
    });
}

document.addEventListener('DOMContentLoaded', init);