import streamlit as st
import sqlite3
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta

# Set page config
st.set_page_config(
    page_title="Market Predictions Dashboard",
    layout="wide",
    initial_sidebar_state="expanded"
)

def load_data(conn):
    """Load data from database."""
    # Get raw data
    market_data = pd.read_sql_query(
        """
        SELECT date, close
        FROM raw_market_data
        WHERE ticker = 'QQQ'
        ORDER BY date
        """,
        conn
    )
    # Convert date to datetime and create a copy to avoid SettingWithCopyWarning
    market_data = market_data.copy()
    market_data['date'] = pd.to_datetime(market_data['date'])
    
    # Calculate split points
    total_days = len(market_data)
    train_end = int(total_days * 0.7)
    val_end = int(total_days * 0.9)
    
    # Create split labels
    market_data['split'] = 'train'
    market_data.iloc[train_end:val_end, market_data.columns.get_loc('split')] = 'validation'
    market_data.iloc[val_end:, market_data.columns.get_loc('split')] = 'test'
    
    # Set index after splits are applied
    market_data.set_index('date', inplace=True)
    
    # Get latest predictions for each model
    predictions = {}
    metrics = {}
    
    for model in ['arima', 'prophet', 'dnn']:
        # Get predictions for test period
        test_start = market_data.index[val_end].strftime('%Y-%m-%d')
        pred_df = pd.read_sql_query(
            f"""
            SELECT *
            FROM {model}_predictions
            WHERE ticker = 'QQQ'
            AND date >= ?
            ORDER BY date
            """,
            conn,
            params=(test_start,)
        )
        # Convert date column to datetime and set as index
        pred_df = pd.DataFrame(pred_df)  # Ensure it's a DataFrame
        pred_df['date'] = pd.to_datetime(pred_df['date'])
        pred_df.set_index('date', inplace=True)
        
        # Convert numeric columns to float
        numeric_cols = ['predicted_value', 'confidence_lower', 'confidence_upper']
        for col in numeric_cols:
            if col in pred_df.columns:
                pred_df[col] = pd.to_numeric(pred_df[col], errors='coerce')
        
        # Convert DNN returns to actual predictions
        if model == 'dnn':
            # Get the close price for each prediction date
            # We need to use current price to predict 3 days ahead
            # So for each prediction date, we use the price from that day
            close_prices = market_data['close'].shift(-3)  # Shift prices back by 3 days
            close_prices = close_prices.reindex(pred_df.index)
            
            # Convert returns to actual prices: current_price * (1 + predicted_return)
            pred_df['predicted_value'] = close_prices * (1 + pred_df['predicted_value']/100)
            pred_df['confidence_upper'] = close_prices * (1 + pred_df['confidence_upper']/100)
            pred_df['confidence_lower'] = close_prices * (1 + pred_df['confidence_lower']/100)
        
        predictions[model] = pred_df
        
        # Get latest metrics
        metrics_df = pd.read_sql_query(
            f"""
            SELECT *
            FROM model_performance
            WHERE model = '{model}'
            AND ticker = 'QQQ'
            ORDER BY date DESC
            LIMIT 1
            """,
            conn,
            parse_dates=['date']
        )
        metrics[model] = metrics_df
    
    return market_data, predictions, metrics

def plot_data_splits(market_data):
    """Create plot showing data splits."""
    fig = go.Figure()
    
    colors = {'train': '#4287f5', 'validation': '#42f554', 'test': '#f54242'}  # Bright colors
    
    for split in ['train', 'validation', 'test']:
        split_data = market_data[market_data['split'] == split]
        fig.add_trace(go.Scatter(
            x=split_data.index,
            y=split_data['close'],
            name=f'{split.capitalize()} Set',
            line=dict(color=colors[split])
        ))
    
    fig.update_layout(
        title=f'QQQ Data Splits (Train/Validation/Test)',
        xaxis_title='Date',
        yaxis_title='Price',
        hovermode='x unified',
        showlegend=True,
        template='presentation',
        height=800
    )
    
    return fig

def plot_test_predictions(market_data, predictions):
    """Create plot showing test set predictions vs real data."""
    fig = go.Figure()
    
    try:
        # Plot test set actual data as solid line
        test_data = market_data[market_data['split'] == 'test']
        fig.add_trace(go.Scatter(
            x=test_data.index,
            y=test_data['close'],
            name='Actual',
            line=dict(color='#ff00ff', width=3)  # Changed to magenta for better visibility on both themes
        ))
        
        colors = {
            'arima': '#4287f5',  # Bright blue
            'prophet': '#42f554',  # Bright green
            'dnn': '#f54242'  # Bright red
        }
        
        opacities = {
            'arima': 0.3,
            'prophet': 0.3,
            'dnn': 0.3
        }
        
        # Plot predictions for each model (test set only)
        for model, pred_df in predictions.items():
            if not pred_df.empty:
                # Filter for test period predictions
                test_pred = pred_df[
                    (pred_df.index >= test_data.index[0]) & 
                    (pred_df.index <= test_data.index[-1]) &
                    (~pred_df['is_future'] if 'is_future' in pred_df.columns else True)
                ]
                
                if not test_pred.empty:
                    # Add prediction line
                    fig.add_trace(go.Scatter(
                        x=test_pred.index,
                        y=test_pred['predicted_value'],
                        name=f'{model.upper()} Prediction',
                        line=dict(color=colors[model], dash='dash', width=2)
                    ))
                    
                    # Calculate rolling prediction error std for confidence intervals
                    test_data_aligned = test_data['close'].reindex(test_pred.index)
                    errors = (test_data_aligned - test_pred['predicted_value']) / test_pred['predicted_value']  # Relative errors
                    error_std = errors.rolling(window=20, min_periods=1).std()
                    
                    # 95% confidence interval (2 standard deviations)
                    upper = test_pred['predicted_value'] * (1 + 2 * error_std)
                    lower = test_pred['predicted_value'] * (1 - 2 * error_std)
                    
                    # Add confidence intervals
                    fig.add_trace(go.Scatter(
                        x=test_pred.index.tolist() + test_pred.index.tolist()[::-1],
                        y=upper.tolist() + lower.tolist()[::-1],
                        fill='toself',
                        fillcolor=f'rgba({int(colors[model][1:3], 16)}, {int(colors[model][3:5], 16)}, {int(colors[model][5:7], 16)}, {opacities[model]})',
                        line=dict(color='rgba(255,255,255,0)'),
                        name=f'{model.upper()} Confidence Interval (Rolling 20d)'
                    ))
    except Exception as e:
        st.error(f"Error plotting predictions: {str(e)}")
    
    fig.update_layout(
        title=f'QQQ Market Predictions',
        xaxis_title='Date',
        yaxis_title='Price',
        hovermode='x unified',
        showlegend=True,
        template='presentation',
        height=800
    )
    
    return fig

def plot_win_rate_comparison(metrics):
    """Create bar chart comparing model win rates vs unconditional."""
    fig = go.Figure()
    
    colors = {
        'arima': '#4287f5',  # Bright blue
        'prophet': '#42f554',  # Bright green
        'dnn': '#f54242'  # Bright red
    }
    
    for model, metric_df in metrics.items():
        if not metric_df.empty:
            win_rate = metric_df['win_rate'].iloc[0]
            uncond_win = metric_df['uncond_win_rate'].iloc[0]
            difference = win_rate - uncond_win
            
            fig.add_trace(go.Bar(
                x=[model.upper()],
                y=[difference],
                name=model.upper(),
                marker_color=colors[model],
                text=[f"{difference:+.1f}%"],  # Add + sign for positive values
                textposition='outside',  # Place text above bars
                textfont=dict(size=18)  # Increase text size even more
            ))
    
    fig.update_layout(
        title={
            'text': 'Model Win Rate vs Market (Percentage Points)',
            'y': 0.95,  # Move title up
            'font': {'size': 24}  # Bigger title font
        },
        yaxis_title={
            'text': 'Win Rate Difference (%)',
            'font': {'size': 16}  # Bigger axis title font
        },
        xaxis_tickfont={'size': 16},  # Bigger x-axis tick labels
        yaxis_tickfont={'size': 16},  # Bigger y-axis tick labels
        showlegend=False,
        template='presentation',
        height=600,  # Make the chart taller
        margin=dict(t=80, b=50)  # More top margin for title
    )
    
    # Add horizontal line at y=0
    fig.add_hline(y=0, line_dash="dash", line_color="gray")
    
    return fig

def display_metrics(metrics):
    """Display comprehensive model performance metrics."""
    st.subheader("Model Performance Metrics")
    
    # Create DataFrame for metrics comparison
    metrics_data = []
    for model, metric_df in metrics.items():
        if not metric_df.empty:
            metrics_data.append({
                'Model': model.upper(),
                'MAE': f"{metric_df['mae'].iloc[0]:.2f}",
                'RMSE': f"{metric_df['rmse'].iloc[0]:.2f}",
                'Win Rate': f"{metric_df['win_rate'].iloc[0]:.1f}%",
                'Loss Rate': f"{metric_df['loss_rate'].iloc[0]:.1f}%",
                'Uncond. Win': f"{metric_df['uncond_win_rate'].iloc[0]:.1f}%",
                'Uncond. Loss': f"{metric_df['uncond_loss_rate'].iloc[0]:.1f}%",
                'Avg Return': f"{metric_df['avg_return'].iloc[0]:.2f}%",
                'P/L Ratio': f"{metric_df['pl_ratio'].iloc[0]:.2f}",
                'Trading Freq': f"{metric_df['trading_freq'].iloc[0]:.1f}%",
                'Trades': int(metric_df['n_trades'].iloc[0])
            })
    
    if metrics_data:
        metrics_df = pd.DataFrame(metrics_data)
        st.dataframe(metrics_df.set_index('Model'), use_container_width=True)
        
        # Add win rate comparison plot
        fig_win_rate = plot_win_rate_comparison(metrics)
        st.plotly_chart(fig_win_rate, use_container_width=True)
    else:
        st.write("No metrics available")

def main():
    st.title("Market Predictions Dashboard")
    
    try:
        # Connect to database
        conn = sqlite3.connect('data/market_data.db')
        
        # Load data
        market_data, predictions, metrics = load_data(conn)
        
        if market_data.empty:
            st.warning("No data available. Please run the data update script.")
            return
        
        # Display last update time
        last_update = market_data.index[-1].strftime('%Y-%m-%d')
        st.write(f"Last data update: {last_update}")
        
        # Create tabs for different visualizations
        tab1, tab2, tab3 = st.tabs(["Data Splits", "Test Predictions", "Model Metrics"])
        
        with tab1:
            # Plot data splits
            fig_splits = plot_data_splits(market_data)
            st.plotly_chart(fig_splits, use_container_width=True, height=800)
            
            # Show split statistics
            cols = st.columns(3)
            for i, split in enumerate(['train', 'validation', 'test']):
                split_data = market_data[market_data['split'] == split]
                with cols[i]:
                    st.metric(
                        f"{split.capitalize()} Set",
                        f"{len(split_data)} samples",
                        f"{len(split_data)/len(market_data)*100:.1f}%"
                    )
        
        with tab2:
            # Plot predictions
            fig_test = plot_test_predictions(market_data, predictions)
            st.plotly_chart(fig_test, use_container_width=True, height=800)
        
        with tab3:
            # Display comprehensive metrics
            display_metrics(metrics)
        
        conn.close()
        
    except Exception as e:
        st.error(f"Error loading data: {str(e)}")

if __name__ == "__main__":
    main()
