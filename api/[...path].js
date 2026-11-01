import { createHmac, timingSafeEqual } from 'node:crypto';
import { createClient } from '@supabase/supabase-js';

const botToken = process.env.TELEGRAM_BOT_TOKEN || '';
const supabaseUrl = process.env.SUPABASE_URL || '';
const serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY || '';
const db = supabaseUrl && serviceKey
  ? createClient(supabaseUrl, serviceKey, { auth: { persistSession: false, autoRefreshToken: false } })
  : null;

function validateTelegramData(raw) {
  if (!botToken || !raw) throw new Error('Telegram sign-in is not configured');
  const params = new URLSearchParams(raw);
  const supplied = params.get('hash');
  params.delete('hash');
  if (!supplied) throw new Error('Missing Telegram signature');
  const check = [...params.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => `${k}=${v}`).join('\n');
  const secret = createHmac('sha256', 'WebAppData').update(botToken).digest();
  const expected = createHmac('sha256', secret).update(check).digest('hex');
  const left = Buffer.from(expected, 'hex');
  const right = Buffer.from(supplied, 'hex');
  if (left.length !== right.length || !timingSafeEqual(left, right)) throw new Error('Invalid Telegram signature');
  let user;
  try { user = JSON.parse(params.get('user') || '{}'); } catch { throw new Error('Invalid Telegram sign-in data'); }
  const authDate = Number(params.get('auth_date') || 0);
  const now = Math.floor(Date.now() / 1000);
  if (!authDate || authDate > now + 60 || now - authDate > 86400) throw new Error('Telegram sign-in expired. Reopen the app.');
  if (!user?.id) throw new Error('Telegram user is missing');
  return user;
}

function fail(res, status, message) { return res.status(status).json({ error: message }); }
function ago(value) {
  const seconds = Math.max(0, Math.floor(Date.now() / 1000) - Math.floor(new Date(value).getTime() / 1000));
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} hr ago`;
  return `${Math.floor(seconds / 86400)} d ago`;
}

async function authenticate(req) {
  if (!db) throw Object.assign(new Error('Server secrets are not configured'), { status: 503 });
  const authorization = req.headers.authorization || '';
  if (!authorization.startsWith('tma ')) throw Object.assign(new Error('Open Sidequest inside Telegram to continue'), { status: 401 });
  let user;
  try { user = validateTelegramData(authorization.slice(4)); }
  catch (e) { throw Object.assign(e, { status: 401 }); }
  const profile = {
    telegram_id: String(user.id),
    name: [user.first_name, user.last_name].filter(Boolean).join(' ').slice(0, 100) || 'Telegram user',
    username: String(user.username || '').slice(0, 64),
    photo_url: String(user.photo_url || '').slice(0, 1000),
  };
  const { data, error } = await db.from('profiles').upsert(profile, { onConflict: 'telegram_id' }).select('telegram_id,name,username,photo_url').single();
  if (error) throw Object.assign(new Error('Could not load your profile'), { status: 500 });
  return data;
}

function safeProfile(profile) {
  return { id: profile.telegram_id, name: profile.name, username: profile.username, photoUrl: profile.photo_url };
}

export default async function handler(req, res) {
  if (req.method === 'OPTIONS') return res.status(204).end();
  try {
    const me = await authenticate(req);
    const pathname = new URL(req.url, 'https://sidequest.invalid').pathname.replace(/\/$/, '') || '/';

    if (req.method === 'GET' && pathname === '/api/me') return res.status(200).json({ user: safeProfile(me) });

    if (req.method === 'GET' && pathname === '/api/people') {
      const [{ data: people, error: peopleError }, { data: follows, error: followsError }] = await Promise.all([
        db.from('profiles').select('telegram_id,name,username,photo_url').neq('telegram_id', me.telegram_id).order('created_at', { ascending: false }).limit(40),
        db.from('follows').select('followed_id').eq('follower_id', me.telegram_id),
      ]);
      if (peopleError || followsError) throw new Error('Could not load people');
      const followed = new Set((follows || []).map(x => x.followed_id));
      return res.status(200).json({ people: (people || []).map(p => ({ ...safeProfile(p), following: followed.has(p.telegram_id) })) });
    }

    if (req.method === 'GET' && pathname === '/api/feed') {
      const { data: rows, error } = await db.from('posts')
        .select('id,user_id,text,created_at,profiles!posts_user_id_fkey(name,username,photo_url),likes(user_id),comments(id,text,user_id,created_at,profiles!comments_user_id_fkey(name))')
        .order('created_at', { ascending: false }).limit(80);
      if (error) throw new Error('Could not load the feed');
      const posts = (rows || []).map(p => {
        const owner = p.profiles || {};
        const comments = (p.comments || []).sort((a, b) => new Date(a.created_at) - new Date(b.created_at));
        return { id: String(p.id), person: p.user_id, name: owner.name || 'Telegram user', handle: owner.username ? `@${owner.username}` : '', photoUrl: owner.photo_url || '', time: ago(p.created_at), text: p.text, likes: (p.likes || []).length, liked: (p.likes || []).some(l => l.user_id === me.telegram_id), comments: comments.slice(0, 3).map(c => ({ name: c.profiles?.name?.split(' ')[0] || 'Neighbor', text: c.text })), replies: comments.length };
      });
      return res.status(200).json({ posts });
    }

    const commentsRoute = pathname.match(/^\/api\/posts\/(\d+)\/comments$/);
    if (req.method === 'GET' && commentsRoute) {
      const { data, error } = await db.from('comments').select('text,created_at,profiles!comments_user_id_fkey(name)').eq('post_id', commentsRoute[1]).order('created_at', { ascending: true }).limit(100);
      if (error) throw new Error('Could not load replies');
      return res.status(200).json({ comments: (data || []).map(c => ({ name: c.profiles?.name?.split(' ')[0] || 'Neighbor', text: c.text, time: ago(c.created_at) })) });
    }

    if (req.method !== 'POST') return fail(res, 405, 'Method not allowed');
    const body = typeof req.body === 'string' ? JSON.parse(req.body || '{}') : (req.body || {});

    if (pathname === '/api/posts') {
      const text = String(body.text || '').trim();
      if (!text || text.length > 500) return fail(res, 400, 'Posts must be between 1 and 500 characters');
      const { data, error } = await db.from('posts').insert({ user_id: me.telegram_id, text }).select('id').single();
      if (error) throw new Error('Could not publish your post');
      return res.status(201).json({ id: data.id });
    }

    const likeRoute = pathname.match(/^\/api\/posts\/(\d+)\/like$/);
    if (likeRoute) {
      const postId = Number(likeRoute[1]);
      const { data: exists, error: checkError } = await db.from('posts').select('id').eq('id', postId).maybeSingle();
      if (checkError || !exists) return fail(res, 404, 'Post not found');
      const { data: existing, error } = await db.from('likes').select('post_id').eq('post_id', postId).eq('user_id', me.telegram_id).maybeSingle();
      if (error) throw new Error('Could not update like');
      if (existing) {
        const result = await db.from('likes').delete().eq('post_id', postId).eq('user_id', me.telegram_id);
        if (result.error) throw new Error('Could not update like');
        return res.status(200).json({ liked: false });
      }
      const result = await db.from('likes').insert({ post_id: postId, user_id: me.telegram_id });
      if (result.error) throw new Error('Could not update like');
      return res.status(200).json({ liked: true });
    }

    if (commentsRoute) {
      const text = String(body.text || '').trim();
      if (!text || text.length > 180) return fail(res, 400, 'Replies must be between 1 and 180 characters');
      const postId = Number(commentsRoute[1]);
      const { data: exists } = await db.from('posts').select('id').eq('id', postId).maybeSingle();
      if (!exists) return fail(res, 404, 'Post not found');
      const { error } = await db.from('comments').insert({ post_id: postId, user_id: me.telegram_id, text });
      if (error) throw new Error('Could not save your reply');
      return res.status(201).json({ ok: true });
    }

    const followRoute = pathname.match(/^\/api\/people\/(\d+)\/follow$/);
    if (followRoute) {
      const target = followRoute[1];
      if (target === me.telegram_id) return fail(res, 400, 'You cannot follow yourself');
      const { data: person, error: personError } = await db.from('profiles').select('telegram_id').eq('telegram_id', target).maybeSingle();
      if (personError || !person) return fail(res, 404, 'Person not found');
      const { data: existing, error } = await db.from('follows').select('followed_id').eq('follower_id', me.telegram_id).eq('followed_id', target).maybeSingle();
      if (error) throw new Error('Could not update follow');
      if (existing) {
        const result = await db.from('follows').delete().eq('follower_id', me.telegram_id).eq('followed_id', target);
        if (result.error) throw new Error('Could not update follow');
        return res.status(200).json({ following: false });
      }
      const result = await db.from('follows').insert({ follower_id: me.telegram_id, followed_id: target });
      if (result.error) throw new Error('Could not update follow');
      return res.status(200).json({ following: true });
    }

    return fail(res, 404, 'Not found');
  } catch (error) {
    const status = Number(error.status) || 500;
    return fail(res, status, status >= 500 ? (status === 503 ? error.message : 'Something went wrong') : error.message);
  }
}

