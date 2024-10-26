import os
from flask import Flask, redirect, url_for, request, session, render_template
from functools import wraps
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import requests
from pymongo import MongoClient
from datetime import datetime, timedelta
import google.generativeai as genai
from typing import List, Dict

# For development only - remove in production
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Change this to a secure secret key

# MongoDB setup
client = MongoClient('mongodb://localhost:27017/')
db = client['stock_portfolio']
users_collection = db['users']

# Google OAuth2 scopes
SCOPES = [
    'https://www.googleapis.com/auth/userinfo.profile',
    'https://www.googleapis.com/auth/userinfo.email',
    'openid'
]

class StockNewsAnalyzer:
    def __init__(self):
        self.news_api_key = "64ef624d6ab84dbfb1159b01a82d83d5"
        self.gemini_api_key = "AIzaSyCjSuhhyEvaTs3ksYuifF37zdSIypyZeAo"
        
        # Configure Gemini
        os.environ["GEMINI_API_KEY"] = self.gemini_api_key
        genai.configure(api_key=os.environ["GEMINI_API_KEY"])
        
        self.generation_config = {
            "temperature": 0.1,
            "top_p": 0.95,
            "top_k": 64,
            "max_output_tokens": 8192,
        }
        self.model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            generation_config=self.generation_config,
        )

    def get_stock_sector(self, company_name: str) -> str:
        """Use Gemini to determine the sector for a given company"""
        prompt = f"""
        What is the primary business sector for the company "{company_name}"?
        Please respond with just the sector name (e.g., TECH, FINANCE, HEALTHCARE, etc.).
        """
        try:
            response = self.model.generate_content(prompt)
            return response.text.strip().upper()
        except Exception as e:
            print(f"Error getting sector for {company_name}: {e}")
            return "UNKNOWN"

    def get_stock_news(self, company_name: str, sector: str) -> List[Dict]:
        """Fetch relevant news for a specific stock and its sector"""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)
        
        base_url = "https://newsapi.org/v2/everything"
        params = {
            'apiKey': self.news_api_key,
            'q': f'"{company_name}" AND (earnings OR merger OR acquisition OR CEO OR stocks)',
            'from': start_date.strftime('%Y-%m-%d'),
            'to': end_date.strftime('%Y-%m-%d'),
            'language': 'en',
            'sortBy': 'relevancy'
        }
        
        try:
            response = requests.get(base_url, params=params)
            response.raise_for_status()
            news_data = response.json()
            
            major_news = []
            articles = news_data.get('articles', [])
            
            for article in articles:
                if not self._is_valid_article(article):
                    continue
                    
                if self._is_major_news(article):
                    major_news.append({
                        'title': article.get('title', ''),
                        'description': article.get('description', ''),
                        'source': article.get('source', {}).get('name', 'Unknown Source'),
                        'url': article.get('url', ''),
                        'publishedAt': article.get('publishedAt', '')
                    })
            
            return major_news
            
        except requests.exceptions.RequestException as e:
            print(f"Error fetching news for {company_name}: {e}")
            return []
        except Exception as e:
            print(f"Unexpected error for {company_name}: {e}")
            return []

    def _is_valid_article(self, article: Dict) -> bool:
        """Check if article has all required fields"""
        if not isinstance(article, dict):
            return False
        
        required_fields = ['title', 'description']
        for field in required_fields:
            if not article.get(field):
                return False
        return True

    def _is_major_news(self, article: Dict) -> bool:
        """Helper method to determine if news is major/significant"""
        try:
            title = article.get('title', '') or ''
            description = article.get('description', '') or ''
            
            text = (title + ' ' + description).lower()
            
            major_keywords = [
                'announces', 'announced', 'merger', 'acquisition', 'earnings',
                'CEO', 'executive', 'lawsuit', 'investigation', 'breakthrough',
                'patent', 'major contract', 'restructuring'
            ]
            
            return any(keyword.lower() in text for keyword in major_keywords)
            
        except Exception as e:
            print(f"Error in _is_major_news: {e}")
            return False

    def analyze_stock(self, stock_news: List[Dict]) -> str:
        """Analyze news using Gemini and generate recommendation"""
        if not stock_news:
            return "Insufficient news data for analysis"
        
        try:
            news_summary = "\n".join([
                f"Title: {news.get('title', 'No Title')}\n"
                f"Description: {news.get('description', 'No Description')}\n"
                f"Source: {news.get('source', 'Unknown Source')}\n"
                f"Date: {news.get('publishedAt', 'No Date')}\n"
                for news in stock_news
            ])
            
            prompt = f"""
            Based on the following recent news articles about a company and its sector, provide a detailed stock recommendation (Buy/Sell/Hold).
            Focus on how these events might impact the stock price and company performance.

            NEWS ARTICLES:
            {news_summary}

            Please analyze the news and provide:
            1. A summary of major events and their potential impact
            2. Key risk factors identified from the news
            3. A clear Buy/Sell/Hold recommendation with rationale
            Keep it concise and brief. Not too long, not too short
            VERY IMPORTANT: Do not use any special characters or markdown formatting. Do not use asterisks (*) or hashtags (#) or dashes (-). Write everything in plain text only.
            """
            
            response = self.model.generate_content(prompt)
            return response.text
            
        except Exception as e:
            return f"Error generating analysis: {e}"

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'credentials' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def credentials_to_dict(credentials):
    return {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }

def get_credentials():
    if 'credentials' not in session:
        return None
    
    credentials = Credentials(**session['credentials'])
    
    if not credentials.valid:
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            session['credentials'] = credentials_to_dict(credentials)
        else:
            return None
    
    return credentials

def get_user_info(credentials):
    userinfo_endpoint = "https://www.googleapis.com/oauth2/v3/userinfo"
    response = requests.get(
        userinfo_endpoint,
        headers={'Authorization': f'Bearer {credentials.token}'}
    )
    if response.status_code == 200:
        return response.json()
    return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login')
def login():
    flow = Flow.from_client_secrets_file(
        'client_secrets.json',
        scopes=SCOPES
    )
    flow.redirect_uri = url_for('oauth2callback', _external=True)
    
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true'
    )
    
    session['state'] = state
    return redirect(authorization_url)

@app.route('/oauth2callback')
def oauth2callback():
    state = session['state']
    
    flow = Flow.from_client_secrets_file(
        'client_secrets.json',
        scopes=SCOPES,
        state=state
    )
    flow.redirect_uri = url_for('oauth2callback', _external=True)
    
    authorization_response = request.url
    flow.fetch_token(authorization_response=authorization_response)
    
    credentials = flow.credentials
    session['credentials'] = credentials_to_dict(credentials)
    
    user_info = get_user_info(credentials)
    if not user_info:
        return "Failed to get user info", 400
    
    existing_user = users_collection.find_one({'email': user_info['email']})
    
    if existing_user:
        users_collection.update_one(
            {'email': user_info['email']},
            {'$set': {'last_login': datetime.utcnow()}}
        )
        session['user_email'] = user_info['email']
        return redirect(url_for('dashboard'))
    else:
        session['user_email'] = user_info['email']
        return redirect(url_for('register_stocks'))

@app.route('/register-stocks', methods=['GET', 'POST'])
@login_required
def register_stocks():
    if request.method == 'POST':
        stocks = request.form.getlist('stocks')
        if len(stocks) < 5:
            return render_template('register_stocks.html', error="Please enter at least 5 stocks")
        
        user_data = {
            'email': session['user_email'],
            'stocks': stocks,
            'created_at': datetime.utcnow(),
            'last_login': datetime.utcnow()
        }
        
        users_collection.insert_one(user_data)
        return redirect(url_for('dashboard'))
    
    return render_template('register_stocks.html')

@app.route('/dashboard')
@login_required
def dashboard():
    credentials = get_credentials()
    if not credentials:
        return redirect(url_for('login'))
    
    user_info = get_user_info(credentials)
    user_data = users_collection.find_one({'email': user_info['email']})
    
    if not user_data:
        return redirect(url_for('register_stocks'))
    
    # Initialize StockNewsAnalyzer
    analyzer = StockNewsAnalyzer()
    
    # Prepare stocks data with sectors
    stocks_to_analyze = []
    stock_analyses = {}
    
    for stock_name in user_data['stocks']:
        # Get sector for each stock using Gemini
        sector = analyzer.get_stock_sector(stock_name)
        
        stocks_to_analyze.append({
            "name": stock_name,
            "sector": sector
        })
    
    # Get news and analysis for each stock
    for stock in stocks_to_analyze:
        try:
            news = analyzer.get_stock_news(stock['name'], stock['sector'])
            analysis = analyzer.analyze_stock(news)
            
            stock_analyses[stock['name']] = {
                'sector': stock['sector'],
                'news': news,
                'analysis': analysis
            }
        except Exception as e:
            print(f"Error analyzing {stock['name']}: {e}")
            stock_analyses[stock['name']] = {
                'sector': stock['sector'],
                'news': [],
                'analysis': f"Error in analysis: {str(e)}"
            }
    
    return render_template('dashboard.html', 
                         user_email=user_data['email'],
                         stocks=user_data['stocks'],
                         stock_analyses=stock_analyses)

@app.route('/logout')
def logout():
    if 'credentials' in session:
        credentials = Credentials(**session['credentials'])
        if credentials and credentials.valid:
            try:
                requests.post(
                    'https://oauth2.googleapis.com/revoke',
                    params={'token': credentials.token},
                    headers={'content-type': 'application/x-www-form-urlencoded'}
                )
            except Exception as e:
                print(f"Error revoking token: {e}")
    
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True, port=5000)